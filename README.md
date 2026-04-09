# 🕸️ Mind Graph

A knowledge graph chat assistant. Converastions are parsed and stored in a Neo4j
graph database with vector embeddings for semantic deduplication and retrieval.

## Prerequisites

- **Neo4j Community** — portable install at `~/neo4j-local/` (with bundled
  JDK 21)
- **Ollama** with `nomic-embed-text` model
- **OpenRouter API key**
- **Python 3.10+** and **Node.js 18+** / **Yarn**

## Setup

### 1. Environment variables

Copy the root env file and add your OpenRouter API key:

```bash
cp .env.example .env
# Edit .env and set OPENROUTER_API_KEY
```

Copy the frontend env file:

```bash
cp frontend/.env.example frontend/.env.local
# Edit frontend/.env.local and set OPENROUTER_API_KEY
# BACKEND_URL defaults to http://localhost:8000 if omitted
```

### 2. Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Frontend dependencies

```bash
cd frontend && yarn install
```

## Running locally

Each service runs in its own terminal.

### Neo4j

```bash
export JAVA_HOME=~/neo4j-local/jdk-21.0.6+7-jre
export NEO4J_HOME=~/neo4j-local/neo4j-community-5.26.1
export PATH=$JAVA_HOME/bin:$NEO4J_HOME/bin:$PATH
neo4j start
# Stop with: neo4j stop
```

### Ollama

```bash
ollama serve
# Pull the embedding model if not already present:
# ollama pull nomic-embed-text
```

### Backend (FastAPI)

```bash
source backend/.venv/bin/activate
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Runs on http://localhost:8000. Health check: `GET /health`.

### Frontend (Next.js)

```bash
cd frontend && yarn dev
```

Runs on http://localhost:3000.

## Architecture

```
Browser → POST /api/chat (Next.js) → POST /api/chat (FastAPI)
    │
    ├─ 1. RAG context: embed query → vector search top-6 → 1-hop expansion
    ├─ 2. LLM reply via OpenRouter (graph context prepended)
    ├─ 3. Entity extraction: LLM extracts JSON {nodes, relationships}
    ├─ 4. Upsert: dedup by cosine similarity (0.92) → embed → MERGE into Neo4j
    └─ Return streamed reply to browser
```

See [CLAUDE.md](./CLAUDE.md) for detailed backend/frontend file references and
configuration options.
