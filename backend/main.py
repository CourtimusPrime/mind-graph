from contextlib import asynccontextmanager
from typing import Callable, Awaitable

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from backend.config import OLLAMA_BASE_URL
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


class ChatResponse(BaseModel):
    reply: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    if db is None or rag is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # 1. Retrieve graph context
    context = await rag.get_context(request.message, embed_fn)

    # 2. Build message history and call the LLM
    messages = request.history + [{"role": "user", "content": request.message}]
    reply = await openrouter.chat(messages, context=context)

    # 3. Extract entities from both the user message and the LLM reply
    combined_text = f"{request.message}\n\n{reply}"
    entities = await extract_entities(combined_text)

    # 4. Upsert extracted entities into the graph
    await db.upsert_entities(entities, request.session_id, embed_fn)

    return ChatResponse(reply=reply)


@app.get("/api/search")
async def search(
    q: str = Query(..., description="Search query"),
    limit: int = Query(8, ge=1, le=50),
):
    if db is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    embedding = await embed_fn(q)
    results = await db.vector_search(embedding, top_k=limit)

    # Strip internal fields before returning
    clean = [
        {k: v for k, v in node.items() if not k.startswith("_")}
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
            RETURN labels(n)[0] AS label, n.name AS name,
                   n.content AS content
            ORDER BY label, name
            """
        )
        nodes = []
        async for record in result:
            nodes.append(dict(record))

    return {"nodes": nodes, "count": len(nodes)}
