# Mind-Graph

A **vector-graph hybrid memory system** for AI assistants. Conversations are automatically parsed, deduplicated, stored in Neo4j with vector embeddings, and retrieved via composite scoring (semantic relevance × graph centrality × temporal recency).

Designed as a **reference implementation** for teams building production AI memory — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design philosophy.

---

## Quick Start (5 commands)

```bash
# 1. Configure
cp .env.example .env && echo "Set OPENROUTER_API_KEY in .env"

# 2. Start Neo4j (portable, no root required)
export JAVA_HOME=~/neo4j-local/jdk-21.0.6+7-jre
export NEO4J_HOME=~/neo4j-local/neo4j-community-5.26.1
export PATH=$JAVA_HOME/bin:$NEO4J_HOME/bin:$PATH
neo4j start

# 3. Start Ollama
ollama serve &  # or in a separate terminal

# 4. Start backend
source backend/.venv/bin/activate
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 5. Start frontend
cd frontend && yarn install && yarn dev
```

Open [http://localhost:3000](http://localhost:3000).

---

## The Five Laws of AI Memory

1. **Hybrid storage is non-negotiable** — Vectors find semantically similar nodes; graph edges capture structure (A *uses* B). Neither alone is sufficient.
2. **Every fact has provenance** — All nodes carry `created_at`, `updated_at`, `session_id`, `access_count`. All edges carry `weight` and `created_at`.
3. **Retrieval is a composite function** — `semantic × 0.60 + graph_centrality × 0.25 + recency × 0.15`
4. **Memory has a lifecycle** — Creation → reinforcement → consolidation → decay → pruning.
5. **The graph is self-maintaining** — Louvain community detection, Note consolidation into Concepts, and fitness-based pruning run automatically.

---

## Architecture

```
Browser → POST /api/chat (Next.js) → POST /api/chat (FastAPI)
    │
    ├─ 1. RAG context: embed query → composite-scored vector search → 1-hop expansion
    ├─ 2. LLM reply via OpenRouter (streamed, graph context prepended)
    ├─ 3. Entity extraction: LLM extracts {nodes, relationships} as JSON (background)
    ├─ 4. Dedup: cosine threshold 0.92 → MERGE into Neo4j with temporal metadata
    ├─ 5. Access recording: retrieved nodes get access_count++
    └─ 6. Every N new nodes: Louvain community detection runs asynchronously
```

### Backend (`backend/`)

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app, all routes |
| `neo4j_client.py` | Graph DB: schema, vector search, upsert, lifecycle queries |
| `rag.py` | `GraphRAG.get_context()` — composite scoring + budget-greedy fill |
| `openrouter.py` | LLM chat (streaming + sync) |
| `extractor.py` | Entity extraction system prompt + JSON schema |
| `embeddings.py` | `embed(text)` — Ollama async call |
| `communities.py` | Louvain community detection + LLM labeling |
| `consolidation.py` | Note cluster → Concept consolidation pipeline |
| `pruning.py` | Fitness-based memory pruning |
| `config.py` | All env vars with defaults |
| `plugins/base.py` | Plugin Protocol interfaces |

### Frontend (`frontend/`)

| Path | Responsibility |
|------|----------------|
| `app/assistant.tsx` | Chat runtime with `AssistantChatTransport` |
| `app/api/chat/route.ts` | Bridge: UIMessage[] → FastAPI SSE stream |
| `app/api/graph/route.ts` | Proxy to `/api/graph` for visualization |
| `components/assistant-ui/graph-panel.tsx` | Sidebar with list/graph view toggle |
| `components/assistant-ui/graph-view.tsx` | Sigma.js force-directed graph (SSR-disabled) |

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/chat` | Stream a chat response with graph-augmented context |
| `GET`  | `/api/search?q=` | Raw cosine vector search |
| `GET`  | `/api/nodes` | All nodes (excludes ExtractionError, Community, consolidated Notes) |
| `GET`  | `/api/graph` | Nodes + edges for visualization |
| `GET`  | `/api/communities` | Detected knowledge communities |
| `GET`  | `/api/memory/health` | Per-type stats: count, avg_access, avg_centrality |
| `POST` | `/api/memory/prune` | Find/delete low-fitness nodes (`dry_run: true` by default) |
| `POST` | `/api/memory/consolidate` | Schedule Note→Concept consolidation |
| `DELETE` | `/api/nodes/{label}/{name}` | Delete node + orphan cleanup |
| `DELETE` | `/api/data` | Clear all graph data |

---

## Configuration

Root `.env` (copy from `.env.example`):

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=openai/gpt-4o-mini

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
EMBEDDING_DIM=768
DEDUP_THRESHOLD=0.92

# Retrieval weights (must approximately sum to 1.0)
RETRIEVAL_SEMANTIC_WEIGHT=0.60
RETRIEVAL_CENTRALITY_WEIGHT=0.25
RETRIEVAL_RECENCY_WEIGHT=0.15
RAG_CONTEXT_CHARS=2500

# Community detection: rerun after this many new nodes
COMMUNITY_RERUN_THRESHOLD=10

# Evaluation API (disabled by default)
ENABLE_EVAL=false
```

Frontend reads `BACKEND_URL` from `frontend/.env.local` (defaults to `http://localhost:8000`).

---

## Evaluation

Run the eval harness against a live backend:

```bash
# Run all fixtures
python -m eval.harness

# Run a specific fixture
python -m eval.harness --fixture project_intro

# Write a JSON report
python -m eval.harness --output results.json
```

Target: overall score ≥ 0.80. Measured metrics:
- **Retrieval precision** — do known entities appear in search results?
- **Dedup accuracy** — do semantically equivalent terms collapse to one node?
- **Extraction recall** — are expected entities created after sending messages?
- **Lifecycle coverage** — do temporal fields, health, and prune endpoints work?

---

## How It Differs from Other Memory Systems

Most AI memory systems are either vector stores (fast recall, no structure) or conversation replay systems (history, no graph). Mind-graph is neither.

### The core structural distinction

Other systems store *what you said*. Mind-graph stores *what you know*.

A user who mentions JWT tokens in 40 different conversations gets one `Concept {name: "JWT", access_count: 40}` with strong weighted edges to related nodes — not 40 stored utterances. The graph compresses episodic memory into structured knowledge automatically.

### 1. Memory has a lifecycle — not just storage

Mem0, Zep, and LlamaIndex graph stores are effectively append-only. Mind-graph has a fitness formula:

```
fitness = (access_count × 2.0)        # reinforcement: each retrieval adds 2 points
        + (log(degree + 1) × 1.5)     # connectivity: hub nodes are harder to prune
        + (exp(-0.693 × age/30) × 3.0) # recency: 30-day half-life
```

Nodes below fitness 1.0 after 14 days become pruning candidates. A cluster of similar Notes automatically consolidates into a Concept. The graph stays healthy without manual curation.

### 2. Retrieval is a composite function, not just cosine

```
composite = semantic × 0.60 + graph_centrality × 0.25 + recency × 0.15
```

Raw cosine similarity ranks "JWT is a token format" and "JWT is used by my API" identically. The **centrality term** promotes hub nodes — a node connected to 10 others is more likely to provide useful cross-domain context than an isolated note. The **recency term** keeps recent conversations relevant even when older nodes have marginally higher cosine scores.

### 3. Relationship weights reinforce over time

Each time the extractor observes an edge, its weight increases by 0.1. A relationship mentioned in 10 conversations has `weight = 2.0`. This makes graph structure a signal about what actually matters to the user — not just what was mentioned once.

### 4. Community detection creates hierarchical structure

Louvain runs on the full weighted edge list after every N new nodes. It creates `Community` nodes that group related concepts and generates LLM summaries for each cluster. Most systems are flat lists of facts; this system has three levels: individual nodes → communities → full graph.

### 5. Provenance on everything

Every node: `created_at`, `updated_at`, `session_id`, `access_count`, `last_accessed`. Every edge: `weight`, `created_at`, `session_id`. MemGPT tracks conversation history but not graph-level provenance. Zep tracks session context but not per-fact temporal metadata.

### Comparison

| System | Primary focus | Graph support | Lifecycle management |
|--------|--------------|---------------|----------------------|
| MemGPT | Long-context pagination | None | None |
| Mem0 | User preference tracking | Minimal | None |
| Zep | Conversation history replay | Some | Basic |
| LlamaIndex KG | Document Q&A | Read-only | None |
| **mind-graph** | **Structured knowledge** | **Native Neo4j** | **Full** |

---

## Deep Dives

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Design philosophy, Cypher patterns, comparison to Mem0/Zep/MemGPT
- [docs/PLUGINS.md](docs/PLUGINS.md) — Plugin authoring guide for custom embedders, extractors, scorers

---

## Prerequisites

- **Neo4j Community 5.x** — portable install at `~/neo4j-local/`
- **Ollama** with `nomic-embed-text` pulled (`ollama pull nomic-embed-text`)
- **OpenRouter API key**
- **Python 3.10+** and **Node.js 18+** / **Yarn**
