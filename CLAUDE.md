# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mind-graph** is a knowledge graph chat assistant. Chat messages are processed through an LLM, and the conversation is used to automatically extract and store entities (Concepts, Projects, Notes, Tags) in a Neo4j graph database with vector embeddings for semantic deduplication and retrieval.

**Services required to run locally:**
- Neo4j Community (bolt://localhost:7687) — portable install at `~/neo4j-local/`
- Ollama with `nomic-embed-text` model (http://localhost:11434)
- OpenRouter API key

**Start services (each in its own terminal or background):**
```bash
# Neo4j (portable, no root needed)
export JAVA_HOME=~/neo4j-local/jdk-21.0.6+7-jre
export NEO4J_HOME=~/neo4j-local/neo4j-community-5.26.1
export PATH=$JAVA_HOME/bin:$NEO4J_HOME/bin:$PATH
neo4j start          # stop: neo4j stop

# Ollama
ollama serve

# FastAPI backend
uvicorn backend.main:app --reload

# Next.js frontend (from frontend/)
cd frontend && yarn dev
```

---

## Commands

### Backend

```bash
# Run the FastAPI backend (from repo root)
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Backfill embeddings for nodes missing them (idempotent)
python -m backend.backfill_embeddings
```

### Frontend

```bash
# From frontend/ directory
yarn install       # First-time setup (uses node-modules linker, not PnP)
yarn dev           # Dev server on http://localhost:3000
yarn build         # Production build
yarn start         # Serve production build

yarn lint          # Biome linter check
yarn lint:fix      # Auto-fix lint issues
yarn format        # Biome format check
yarn format:fix    # Auto-fix formatting
```

---

## Architecture

### Request Flow

```
Browser → POST /api/chat (Next.js) → POST /api/chat (FastAPI)
    │
    ├─ 1. RAG context: embed query → vector search top-6 → 1-hop expansion
    ├─ 2. LLM reply via OpenRouter (with graph context prepended)
    ├─ 3. Entity extraction: LLM extracts JSON {nodes, relationships}
    ├─ 4. Upsert: dedup by cosine similarity (threshold 0.92) → embed → MERGE into Neo4j
    └─ Return reply
    │
Next.js route converts plain response to Vercel AI stream format
AssistantUI <Thread /> renders the streamed response
```

### Backend (`backend/`)

| File | Responsibility |
|------|---------------|
| `main.py` | FastAPI app, routes (`/health`, `/api/chat`, `/api/search`, `/api/nodes`) |
| `neo4j_client.py` | Graph DB: schema init, vector search, 1-hop expansion, upsert with dedup |
| `rag.py` | `GraphRAG.get_context()` — builds context string from vector search + neighbors |
| `openrouter.py` | LLM chat (with context injection) and entity extraction (JSON mode) |
| `extractor.py` | System prompt + JSON schema for entity extraction |
| `embeddings.py` | `embed(text)` — single async call to Ollama |
| `config.py` | All env vars (`NEO4J_*`, `OPENROUTER_*`, `OLLAMA_*`, `EMBEDDING_DIM`, `DEDUP_THRESHOLD`) |

**Node types:** `Concept`, `Project`, `Note`, `Tag` (each has its own vector index)

**Relationship format:** `UPPER_SNAKE_CASE` strings (e.g., `USES_CONCEPT`, `BELONGS_TO`)

### Frontend (`frontend/`)

| Path | Responsibility |
|------|---------------|
| `app/assistant.tsx` | Chat runtime: `useChatRuntime()` → `AssistantChatTransport` → `/api/chat` |
| `app/api/chat/route.ts` | Bridge: receives `UIMessage[]`, calls FastAPI, returns Vercel AI stream |
| `app/page.tsx` | Root page — renders `<Assistant />` |
| `components/assistant-ui/` | AssistantUI thread, markdown, attachments, tool components |
| `components/ui/` | Radix UI primitives (Button, Dialog, Tooltip, etc.) |

**Key dependency:** `@assistant-ui/react` + `@assistant-ui/react-ai-sdk` for chat UI primitives. The frontend does not call OpenRouter directly — all LLM calls go through FastAPI.

### Configuration

Root `.env` (copy from `.env.example`):
```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
OPENROUTER_API_KEY=sk-...
OPENROUTER_MODEL=openai/gpt-4o-mini
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
EMBEDDING_DIM=768
DEDUP_THRESHOLD=0.92
```

Frontend reads `BACKEND_URL` from `frontend/.env.local` (defaults to `http://localhost:8000` in code).

### Yarn / Turbopack Note

The frontend uses Yarn with `nodeLinker: node-modules` (in `frontend/.yarnrc.yml`). This is required — Turbopack is incompatible with Yarn PnP's virtual filesystem. Do not change the linker.
