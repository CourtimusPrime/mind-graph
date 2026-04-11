import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import Callable, Awaitable

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.config import (
    OLLAMA_BASE_URL,
    COMMUNITY_RERUN_THRESHOLD,
    ENABLE_EVAL,
)
from backend.neo4j_client import Neo4jClient
from backend.embeddings import embed
from backend.rag import GraphRAG
from backend.extractor import extract_entities
from backend import openrouter

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

db: Neo4jClient | None = None
rag: GraphRAG | None = None
embed_fn: Callable[[str], Awaitable[list[float]]] = embed

# Counter for triggering periodic community detection
_nodes_since_last_community_run: int = 0


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, rag

    db = Neo4jClient()
    await db._init_schema()

    # Verify Ollama is reachable
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get(f"{OLLAMA_BASE_URL}/api/tags")
    except Exception:
        raise RuntimeError(
            f"Cannot reach Ollama at {OLLAMA_BASE_URL}. "
            "Run 'ollama serve' and ensure the model is pulled."
        )

    rag = GraphRAG(db)

    yield

    await db.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Mind Graph", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    history: list[dict] = []


class PruneRequest(BaseModel):
    dry_run: bool = True
    min_fitness: float = 1.0
    min_age_days: int = 14


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

async def _run_community_detection() -> None:
    """Lazy-import wrapper so communities.py isn't loaded at startup."""
    if db is None:
        return
    try:
        from backend.communities import detect_communities
        await detect_communities(db)
    except Exception as exc:
        print(f"[community detection] error: {exc}")


async def _extract_and_upsert(combined_text: str, session_id: str) -> None:
    """
    Background task: extract entities from conversation text and upsert to Neo4j.

    A single extraction pass is used (no double-pass pre-check). The extractor
    prompt already handles project-qualified Note naming when a project is present
    in the text. The session's existing project is still used as a hint for
    subsequent messages after the project node is established.

    Errors are persisted as ExtractionError nodes for observability.
    """
    global _nodes_since_last_community_run

    text_hash = sha256(combined_text.encode()).hexdigest()[:16]
    try:
        project_hint = (
            await db.get_session_project(session_id) if db is not None else None
        )
        entities = await extract_entities(combined_text, project_hint=project_hint)
        if db is not None:
            new_count = await db.upsert_entities(entities, session_id, embed_fn)
            _nodes_since_last_community_run += new_count
            if _nodes_since_last_community_run >= COMMUNITY_RERUN_THRESHOLD:
                _nodes_since_last_community_run = 0
                asyncio.create_task(_run_community_detection())
    except Exception as exc:
        print(f"[entity extraction] error: {exc}")
        if db is not None:
            await db.log_extraction_error(session_id, text_hash, str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    if db is None or rag is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # 1. Retrieve composite-scored graph context
    context = await rag.get_context(request.message, embed_fn)

    # 2. Build message history
    messages = request.history + [{"role": "user", "content": request.message}]

    # 3. Stream the LLM reply; schedule extraction after stream completes
    async def generate():
        chunks: list[str] = []
        async for chunk in openrouter.chat_stream(messages, context=context):
            chunks.append(chunk)
            yield f"data: {chunk}\n\n"

        full_reply = "".join(chunks)
        combined_text = f"{request.message}\n\n{full_reply}"
        asyncio.create_task(_extract_and_upsert(combined_text, request.session_id))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/search")
async def search(
    q: str = Query(..., description="Search query"),
    limit: int = Query(8, ge=1, le=50),
):
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    embedding = await embed_fn(q)
    results = await db.vector_search(embedding, top_k=limit)

    _exclude = {"embedding"}
    clean = [
        {k: v for k, v in node.items() if not k.startswith("_") and k not in _exclude}
        for node in results
    ]
    return {"results": clean, "count": len(clean)}


@app.get("/api/nodes")
async def list_nodes():
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    async with db._driver.session() as session:
        result = await session.run(
            """
            MATCH (n)
            WHERE n.name IS NOT NULL
              AND NOT n:ExtractionError
              AND NOT n:Community
              AND NOT coalesce(n.consolidated, false)
            RETURN labels(n)[0] AS label,
                   n.name AS name,
                   n.content AS content,
                   toString(n.created_at) AS created_at,
                   coalesce(n.access_count, 0) AS access_count
            ORDER BY n.updated_at DESC, label, name
            """
        )
        nodes = [dict(r) async for r in result]

    return {"nodes": nodes, "count": len(nodes)}


@app.get("/api/graph")
async def get_graph():
    """Return all nodes and edges for the force-directed graph visualization."""
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return await db.get_graph_data()


@app.get("/api/communities")
async def get_communities():
    """Return detected knowledge communities with LLM-generated labels."""
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    communities = await db.get_communities()
    return {"communities": communities, "count": len(communities)}


@app.delete("/api/data")
async def clear_data():
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    await db.clear_all_data()
    return {"status": "cleared"}


@app.delete("/api/nodes/{label}/{name}")
async def delete_node(label: str, name: str):
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    deleted = await db.delete_node(label, name)
    return {"status": "deleted", "deleted_count": deleted}


# ---------------------------------------------------------------------------
# Memory lifecycle routes (Phase 3)
# ---------------------------------------------------------------------------

@app.get("/api/memory/health")
async def memory_health():
    """Per-type node statistics: count, avg_access, avg_centrality."""
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return await db.get_memory_health()


@app.post("/api/memory/prune")
async def prune_memory(request: PruneRequest = PruneRequest()):
    """
    Find and optionally delete low-fitness nodes.
    dry_run=true (default) reports candidates without deleting anything.
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    from backend.pruning import run_pruning
    return await run_pruning(
        db,
        dry_run=request.dry_run,
        min_fitness=request.min_fitness,
        min_age_days=request.min_age_days,
    )


@app.post("/api/memory/consolidate")
async def consolidate_memory():
    """Schedule async consolidation of similar Note clusters into Concepts."""
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    async def _run():
        from backend.consolidation import run_consolidation
        await run_consolidation(db, embed_fn)

    asyncio.create_task(_run())
    return {"status": "consolidation scheduled"}


# ---------------------------------------------------------------------------
# Eval route (only when ENABLE_EVAL=true)
# ---------------------------------------------------------------------------

if ENABLE_EVAL:
    @app.post("/api/eval")
    async def run_eval(fixture: str = Query("default", description="Fixture name")):
        """Run evaluation harness against a named fixture. ENABLE_EVAL must be true."""
        try:
            from eval.harness import run_harness
            report = await run_harness(fixture=fixture)
            return report
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
