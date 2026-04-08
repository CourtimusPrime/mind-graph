# PRD: Vector-Graph Hybrid Upgrade

**Project:** `knowledge-graph`\
**Status:** Ready for implementation\
**Scope:** Backend only — no frontend changes required

---

## Background

The existing project is a personal knowledge graph backed by Neo4j Community
(self-hosted on `/mnt/f/`), a FastAPI backend, and a React/D3 frontend. The AI
chatbot auto-extracts entities from conversations and stores them as typed nodes
(`Concept`, `Project`, `Note`, `Tag`) with relationships.

The current RAG retrieval in `rag.py` uses keyword-based full-text search
against node names and descriptions. This has two failure modes:

1. **Semantic misses** — "ML" and "machine learning" are unrelated as far as the
   index is concerned
2. **Duplicate nodes** — the extractor creates separate nodes for the same
   concept described with different words, fragmenting the graph over time

This PRD defines the upgrade to a **vector-graph hybrid** architecture that
fixes both.

---

## Goals

1. Embed every node on upsert using a configurable embedding provider
2. Replace keyword RAG with vector similarity search, followed by 1-hop graph
   expansion
3. Add semantic deduplication on node upsert — if a near-identical node already
   exists, merge rather than create
4. Zero changes to the frontend or Docker setup

---

## Non-Goals

- No changes to `frontend/`
- No changes to `docker-compose.yml`
- No new external services beyond an optional Ollama install
- No changes to `extractor.py` logic (only its caller changes)

---

## Architecture After This Change

```
User message
  │
  ▼
embeddings.py — embed query
  │
  ▼
neo4j_client.py — vector similarity search (top-K nodes)
  │
  ▼
neo4j_client.py — expand 1 hop along graph edges from each hit
  │
  ▼
rag.py — format context string (replaces keyword search)
  │
  ▼
openrouter.py — chat with context
  │
  ▼
extractor.py — extract entities (unchanged)
  │
  ▼
neo4j_client.py — dedup check before upsert
  │
  ├── cosine similarity vs existing nodes > threshold → MERGE
  └── no match → CREATE new node, then embed and store
```

---

## New File: `backend/embeddings.py`

Create this file. It must expose a single async function
`embed(text: str) -> list[float]`.

### Implementation

Uses Ollama exclusively. No external API keys required.

### Ollama implementation

- Endpoint: `POST http://localhost:11434/api/embeddings`
- No auth required
- Request body: `{"model": "nomic-embed-text", "prompt": text}`
- Return: `response["embedding"]`
- Use `httpx.AsyncClient`

### Dimension mismatch handling

The Neo4j vector index dimension is set once on creation. If the configured
dimension doesn't match an existing index, Neo4j will raise an error. Add a
clear exception message:
`"Embedding dimension mismatch. If you switched providers, drop and recreate the Neo4j vector indexes."`

### Text to embed

When embedding a node, concatenate its fields in this format:

```
{label}: {name}. {description}
```

Examples:

- `Concept: machine learning. A subset of AI that learns from data`
- `Tag: python`
- `Project: Marvin. B2B SaaS AI chatbot built on OpenRouter`

---

## Changes to `backend/config.py`

Add these new env vars with defaults:

```python
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EMBEDDING_DIM   = int(os.getenv("EMBEDDING_DIM", "768"))       # nomic-embed-text outputs 768
DEDUP_THRESHOLD = float(os.getenv("DEDUP_THRESHOLD", "0.92"))  # cosine similarity for merge
```

---

## Changes to `.env.example`

Add:

```
# Embeddings (Ollama — run: ollama pull nomic-embed-text)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
EMBEDDING_DIM=768
DEDUP_THRESHOLD=0.92
```

---

## Changes to `backend/requirements.txt`

No new packages needed. `httpx` is already present and handles both embedding
providers.

---

## Changes to `backend/neo4j_client.py`

### Schema init — add vector indexes

In `_init_schema`, after the existing index creation calls, add one vector index
per node type:

```cypher
CREATE VECTOR INDEX concept_embedding IF NOT EXISTS
FOR (n:Concept) ON (n.embedding)
OPTIONS { indexConfig: { `vector.dimensions`: $dim, `vector.similarity_function`: 'cosine' } }
```

Repeat for `Project`, `Note`, `Tag`. Pass `EMBEDDING_DIM` as a parameter.

### New method: `find_similar_node(label, embedding, threshold) -> dict | None`

```cypher
CALL db.index.vector.queryNodes($index_name, 1, $embedding)
YIELD node, score
WHERE score >= $threshold
RETURN node, score
LIMIT 1
```

- `index_name` is `{label.lower()}_embedding`
- Returns the node dict if found above threshold, else `None`
- This is used for deduplication before upsert

### New method: `vector_search(embedding, top_k=8) -> list[dict]`

Search across all four node type indexes, merge results, sort by score, return
top `top_k`:

```cypher
CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
YIELD node, score
RETURN labels(node)[0] AS type,
       coalesce(node.name, node.title) AS name,
       coalesce(node.description, node.content, '') AS description,
       score
```

Run this for each of the four indexes, combine all results in Python, sort
descending by score, deduplicate by name, return top `top_k`.

### New method: `expand_from_nodes(names: list[str]) -> list[dict]`

Given a list of node names from the vector search, fetch their 1-hop neighbours:

```cypher
MATCH (n)-[r]-(neighbour)
WHERE (n.name IN $names OR n.title IN $names)
  AND (neighbour:Concept OR neighbour:Project OR neighbour:Note OR neighbour:Tag)
RETURN labels(neighbour)[0] AS type,
       coalesce(neighbour.name, neighbour.title) AS name,
       coalesce(neighbour.description, neighbour.content, '') AS description,
       type(r) AS relationship
LIMIT 20
```

### Changes to `upsert_entities`

The method signature must change to accept embeddings:

```python
async def upsert_entities(self, entities: dict, session_id: str, embed_fn) -> None:
```

For each node before the `MERGE`:

1. Call `embed_fn(node_text)` to get the embedding
2. Call `find_similar_node(label, embedding, DEDUP_THRESHOLD)`
3. If a match is found: update its description if the new one is more
   informative (longer), set its embedding to the new one, skip creation
4. If no match: proceed with the existing `MERGE` logic, then set
   `n.embedding = $embedding` in the `ON CREATE SET` clause

---

## Changes to `backend/rag.py`

Replace the entire implementation. The new `get_context` method signature
becomes:

```python
async def get_context(self, user_message: str, embed_fn) -> str:
```

Steps:

1. `embedding = await embed_fn(user_message)`
2. `hits = self.db.vector_search(embedding, top_k=6)`
3. `names = [h["name"] for h in hits]`
4. `neighbours = self.db.expand_from_nodes(names)`
5. Format and return a context string

Format the context string as:

```
Relevant from your knowledge graph:

[Concept] machine learning — A subset of AI that learns from data
[Project] Marvin — B2B SaaS AI chatbot
  └─ TAGGED_WITH → [Tag] typescript
  └─ RELATED_TO → [Concept] OpenRouter

[Tag] python
```

Direct hits first, then neighbours indented under their parent with the
relationship type shown. Cap total context at 1500 characters — truncate
neighbour list first if needed.

Remove the `_keywords` method entirely.

---

## Changes to `backend/main.py`

### Startup

In the `lifespan` function, instantiate the embedding function:

```python
from embeddings import embed as embed_fn
```

Pass it to `GraphRAG` and make it available to route handlers.

### Update `GraphRAG` instantiation

`rag.py` no longer needs the `or_client`. Remove that dependency from
`GraphRAG.__init__`.

### Update `/api/chat` handler

- `context = await rag.get_context(req.message, embed_fn)` (was synchronous
  before)
- `db.upsert_entities(entities, session_id, embed_fn)` (new signature)

### New route: `GET /api/search`

```python
@app.get("/api/search")
async def semantic_search(q: str, limit: int = 10):
    embedding = await embed_fn(q)
    return db.vector_search(embedding, top_k=limit)
```

Exposes semantic search to the frontend for future use.

---

## Changes to `backend/openrouter.py`

No changes required.

---

## Changes to `backend/extractor.py`

No changes required.

---

## Migration: Backfilling embeddings on existing nodes

Add a one-time CLI script at `backend/backfill_embeddings.py`:

```
python backfill_embeddings.py
```

It should:

1. `MATCH (n) WHERE (n:Concept OR n:Project OR n:Note OR n:Tag) AND n.embedding IS NULL`
2. For each node, build the embed text, call `embed_fn`, set `n.embedding`
3. Print progress: `Embedded 12/47 nodes...`
4. On completion: `Done. X nodes embedded.`

This is safe to re-run — the `IS NULL` filter skips already-embedded nodes.

---

## Acceptance Criteria

- [ ] `embeddings.py` successfully calls Ollama's `nomic-embed-text` and returns
      a 768-dim float list
- [ ] A clear error is raised on startup if Ollama is unreachable, with the
      message:
      `"Cannot connect to Ollama at {OLLAMA_BASE_URL}. Is Ollama running? Try: ollama serve"`
- [ ] Sending the same concept twice (e.g. "ML" then "machine learning") results
      in one node, not two
- [ ] `GET /api/search?q=neural+networks` returns semantically relevant nodes
      even if the word "neural" doesn't appear in any node name
- [ ] RAG context in chat responses includes graph neighbours, not just direct
      matches
- [ ] `python backfill_embeddings.py` embeds all existing nodes without error
- [ ] No changes to `frontend/` directory
- [ ] No changes to `docker-compose.yml`
- [ ] WSL note: Ollama running on Windows is reachable from WSL2 at
      `http://$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}'):11434`
      — document this in a `## WSL Note` section in the README

---

## Suggested Implementation Order

1. `config.py` — add env vars
2. `embeddings.py` — new file, both providers
3. `neo4j_client.py` — vector indexes, `find_similar_node`, `vector_search`,
   `expand_from_nodes`, update `upsert_entities`
4. `rag.py` — full rewrite
5. `main.py` — wire up `embed_fn`, update callers, add `/api/search`
6. `backfill_embeddings.py` — migration script
7. `.env.example` — add new vars
