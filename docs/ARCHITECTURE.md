# Mind-Graph: Architecture Reference

This document is the canonical reference for the vector-graph hybrid memory
system implemented in mind-graph. It explains not just _what_ the system does
but _why_ each design decision was made — so that other teams can adapt the
patterns rather than re-discover them.

---

## The Five Laws of AI Memory

Every implementation decision in this system enforces one of these laws:

1. **Hybrid storage is non-negotiable.** Vectors catch semantic similarity;
   graphs capture structural relationships. Pure vector stores cannot represent
   that "JWT" is _used by_ "the API" — only that the two concepts are related.
   Pure graphs cannot do fuzzy recall across terminology variations. Neither
   alone is sufficient.

2. **Every fact has provenance.** A memory without a timestamp and a source is
   untrustworthy. Every node carries `created_at`, `updated_at`, `session_id`,
   and `access_count`. Every relationship carries `created_at`, `session_id`,
   and `weight`. These fields make the graph auditable and enable lifecycle
   management.

3. **Retrieval quality is a composite function.** Raw cosine similarity is
   necessary but not sufficient. A highly connected hub node that was retrieved
   frequently last week is more valuable than an isolated node that happens to
   have a slightly higher cosine score. The composite formula:
   `semantic × 0.60 + centrality × 0.25 + recency × 0.15`.

4. **Memory has a lifecycle.** Creation → reinforcement → consolidation → decay
   → pruning. A node that is never retrieved should not persist forever. A
   cluster of similar notes should eventually consolidate into a single concept.
   The system is designed to manage this automatically.

5. **The graph must be self-maintaining.** Related memories consolidate via the
   Louvain algorithm + LLM summarization. Unused memories decay via a fitness
   formula with a 30-day half-life. The system never requires manual curation to
   stay healthy.

---

## The Memory Stack

```
                  ┌─────────────────────────────────────┐
User message ──►  │  1. INGEST                          │
                  │     LLM streaming reply             │
                  │     (entity extraction as bg task)  │
                  └──────────────┬──────────────────────┘
                                 │
                  ┌──────────────▼──────────────────────┐
                  │  2. DEDUPLICATE                      │
                  │     Embed text → cosine search      │
                  │     Threshold 0.92 → MERGE          │
                  └──────────────┬──────────────────────┘
                                 │
                  ┌──────────────▼──────────────────────┐
                  │  3. STORE                            │
                  │     Neo4j MERGE with temporals       │
                  │     Relationship weights updated     │
                  └──────────────┬──────────────────────┘
                                 │
                  ┌──────────────▼──────────────────────┐
                  │  4. RETRIEVE (on next message)       │
                  │     Composite score search           │
                  │     1-hop graph expansion            │
                  │     Budget-greedy context fill       │
                  └──────────────┬──────────────────────┘
                                 │
                  ┌──────────────▼──────────────────────┐
                  │  5. LIFECYCLE (background)           │
                  │     Community detection (Louvain)    │
                  │     Consolidation (Note → Concept)   │
                  │     Pruning (low-fitness nodes)       │
                  └─────────────────────────────────────┘
```

---

## Node Types and Their Semantics

| Label     | Represents                                 | Naming convention                    |
| --------- | ------------------------------------------ | ------------------------------------ |
| `Concept` | Ideas, topics, technologies, people, roles | Canonical, title-case                |
| `Project` | Named initiatives, apps, stories           | As named by user                     |
| `Note`    | Specific facts, decisions, plot points     | Descriptive phrase, project-prefixed |
| `Tag`     | Categories or labels                       | Lowercase, kebab-case                |

**Why four types?** Each has a different retrieval behavior and lifecycle.
Concepts are long-lived hubs. Projects anchor Notes. Notes are the raw episodic
memory that may consolidate into Concepts. Tags are pure organizational
structure.

---

## Deduplication

The 0.92 cosine threshold is the core of Law 1. When a new node arrives:

1. Embed the text (`name + ": " + content`)
2. Query the vector index for the top match in the same label class
3. If score ≥ 0.92, reuse the existing node (update its embedding if missing,
   increment `access_count`)
4. If score < 0.92, MERGE a new node (ON CREATE sets all temporals; ON MATCH
   increments `access_count`)

**Why 0.92?** Empirically, 0.90–0.93 is the inflection point where "machine
learning" and "ML" collapse while "machine learning" and "deep learning" remain
separate. The value is configurable via `DEDUP_THRESHOLD`.

**Why not edit distance or BM25?** Because the domain is conversational and
terminology varies wildly. "The inciting incident" and "what triggers the story"
are semantically identical but have zero lexical overlap.

---

## Composite Retrieval Scoring

Raw cosine similarity ranks nodes by how similar they are to the query
embedding. The composite formula adds two corrections:

```
composite = (cosine × 0.60)
          + (log(degree + 1) / 10 × 0.25)
          + (exp(-0.693 × age_days / 7) × 0.15)
```

**Centrality term:** `log(degree + 1) / 10` — a node connected to 10 others
scores ~0.24 (at max weight), a singleton scores 0. Hubs are more likely to
provide useful cross-domain context. The log compresses the range so very
high-degree nodes don't dominate.

**Recency term:** `exp(-0.693 × age_days / 7)` — half-life of 7 days. A node
created today scores 1.0; one created 7 days ago scores 0.5; 30 days ago scores
~0.05. This ensures recent conversations are preferred for retrieval even if
older nodes have higher cosine similarity.

**Why these weights?** 60/25/15 gives semantic similarity primary authority
while letting the graph and time signal break ties. All three weights are
configurable via env vars.

---

## 1-Hop Graph Expansion

After vector search returns top-k direct hits, the system expands one hop:

```cypher
MATCH (n)-[r]-(neighbour)
WHERE n.name IN $names
RETURN n.name AS source, type(r) AS rel_type,
       neighbour.name AS neighbour_name,
       neighbour.content AS neighbour_content
```

This captures the structural relationships that pure vector search cannot:
"MindGraph USES_CONCEPT Neo4j" tells the LLM something a vector match alone
cannot convey. Neighbour content fills the context budget after direct hits.

**Context budget:** 2500 characters (configurable via `RAG_CONTEXT_CHARS`).
Lines are added greedily in composite-score order, so the highest-value nodes
always make it in.

---

## Memory Lifecycle

### Fitness Formula

```
fitness = (access_count × 2.0)        # reinforcement
        + (log(degree + 1) × 1.5)     # connectivity
        + (exp(-0.693 × age/30) × 3.0) # recency (30-day half-life)
```

A freshly created node with no accesses scores 3.0. One accessed twice with 5
connections and 30 days old scores:
`(2 × 2.0) + (log(6) × 1.5) + (exp(-0.693) × 3.0)` ≈ `4.0 + 2.7 + 1.5` = 8.2.
Nodes below fitness 1.0 after 14 days are pruning candidates.

### Consolidation

Note clusters with pairwise similarity in 0.80–0.91 (related but below the dedup
threshold) are candidates for consolidation. Groups of 3+ Notes are summarized
by the LLM into a new Concept node. Original Notes are preserved with
`consolidated=true` and linked via `CONSOLIDATED_INTO` edges.

**Why 0.80–0.91?** Below 0.80, the notes are distinct concepts. Above 0.91, they
should have been deduped at ingestion. The 0.80–0.91 band is the "same theme,
different phrasing" zone.

### Community Detection

The Louvain algorithm (via networkx) runs on the full edge list after every 10
new nodes. Communities with 3+ members get an LLM-generated label. Communities
are stored as `Community` nodes with `BELONGS_TO_COMMUNITY` edges and are
excluded from the default node listing.

**Why Louvain?** It's parameter-free (no need to specify k), handles weighted
edges natively, and runs in O(n log n) time — fast enough for graphs up to
~10,000 nodes without dedicated infrastructure.

---

## Cypher Patterns Reference

### Temporal upsert (ON CREATE / ON MATCH)

```cypher
MERGE (n:Concept {name: $name})
ON CREATE SET n.embedding = $embedding,
              n.session_id = $session_id,
              n.content = $content,
              n.created_at = datetime(),
              n.updated_at = datetime(),
              n.access_count = 0
ON MATCH  SET n.embedding = CASE WHEN $content <> '' THEN $embedding ELSE n.embedding END,
              n.content    = CASE WHEN $content <> '' THEN $content ELSE n.content END,
              n.updated_at = datetime(),
              n.access_count = coalesce(n.access_count, 0) + 1
```

### Composite-scored vector search

```cypher
CALL db.index.vector.queryNodes($index, $k, $embedding)
YIELD node, score AS vector_score
OPTIONAL MATCH (node)-[r]-()
WITH node, vector_score, count(r) AS degree
WITH node, vector_score, degree,
     exp(-0.693 * toFloat(
         datetime().epochMillis - coalesce(node.created_at, datetime()).epochMillis
     ) / 86400000.0 / 7.0) AS recency_score
WITH node,
     (vector_score * $semantic_w)
     + (log(degree + 1) / 10.0 * $centrality_w)
     + (recency_score * $recency_w) AS composite_score
ORDER BY composite_score DESC
RETURN node, composite_score
LIMIT $k
```

### Weighted relationship with reinforcement

```cypher
MATCH (a {name: $src}), (b {name: $tgt})
MERGE (a)-[r:REL_TYPE]->(b)
ON CREATE SET r.created_at = datetime(), r.session_id = $session_id, r.weight = 1.0
ON MATCH  SET r.weight = coalesce(r.weight, 1.0) + 0.1, r.last_seen = datetime()
```

### Fitness-based pruning candidates

```cypher
MATCH (n)
WHERE n.name IS NOT NULL AND NOT n:ExtractionError AND NOT n:Community
OPTIONAL MATCH (n)-[r]-()
WITH n, count(r) AS degree,
     toFloat(datetime().epochMillis - coalesce(n.created_at, datetime()).epochMillis)
         / 86400000.0 AS age_days
WITH n, degree, age_days,
     (coalesce(n.access_count, 0) * 2.0)
     + (log(degree + 1) * 1.5)
     + (exp(-0.693 * age_days / 30.0) * 3.0) AS fitness
WHERE fitness < 1.0 AND age_days >= 14
RETURN n, fitness ORDER BY fitness ASC
```

### Orphan cleanup on delete

```cypher
MATCH (n) WHERE $label IN labels(n) AND n.name = $name
OPTIONAL MATCH (n)-[]-(neighbor)
WITH n, collect(DISTINCT neighbor) AS neighbors
UNWIND neighbors AS neighbor
OPTIONAL MATCH (neighbor)-[]-(other) WHERE other <> n
WITH n, neighbor, count(other) AS other_connections
WITH n, collect(CASE WHEN other_connections = 0 THEN neighbor ELSE null END) AS orphans
DETACH DELETE n
WITH orphans UNWIND orphans AS orphan
DETACH DELETE orphan
RETURN size(orphans) + 1 AS deleted_count
```

---

## Design Decisions

### Why Neo4j and not a relational DB with pgvector?

Relationship traversal is a first-class operation in Neo4j (O(degree) per hop).
In a relational DB, any graph traversal is a join or recursive CTE — expensive
and awkward to express. The 1-hop expansion that brings neighbour context is a
single Cypher MATCH. In SQL it would be a self-join on an edge table.

pgvector is excellent for pure vector search, but the structural query
expressiveness of Neo4j's Cypher is not replicable in SQL without significant
engineering overhead.

### Why not FAISS or Qdrant?

FAISS and Qdrant are pure vector databases. They have no relationship model. You
cannot express "JWT is USED_BY the API" in a vector database — you can only say
"JWT and API are 0.73 cosine similar." The graph structure is the core value,
not the vector search.

### Why Ollama/nomic-embed-text and not OpenAI embeddings?

Privacy, cost, and latency. `nomic-embed-text` runs locally, embeds ~200
tokens/second on a CPU, and costs nothing. For a personal knowledge graph, the
768-dimension local model is more than adequate. The embedding model is
pluggable via `EmbedPlugin`.

### Why not MemGPT / Mem0 / Zep?

| System         | Primary focus            | Graph support    | Lifecycle management |
| -------------- | ------------------------ | ---------------- | -------------------- |
| MemGPT         | Long-context pagination  | None             | None                 |
| Mem0           | User preference tracking | Minimal          | None                 |
| Zep            | Conversation history     | Some             | Basic                |
| **mind-graph** | **Structured knowledge** | **Native Neo4j** | **Full lifecycle**   |

Mind-graph is optimized for _structured knowledge_ about projects, concepts, and
relationships — not for conversation history replay. The community detection,
consolidation, and pruning pipeline have no analogue in these systems.

### Why fire-and-forget entity extraction?

Entity extraction adds ~2–5 seconds of LLM latency. If it were synchronous, the
user would wait after every message. By using `asyncio.create_task`, the reply
streams immediately and extraction runs in parallel. The downside is that
extraction errors are logged to the graph rather than surfaced to the user.

### Why the 0.92 dedup threshold and not 0.95 or 0.85?

Testing across diverse conversation styles showed:

- At 0.95: "machine learning" and "ML" remain separate nodes
- At 0.85: "machine learning" and "deep learning" collapse (incorrect)
- At 0.92: the two ML variants collapse; DL remains separate

The threshold is configurable because the right value depends on the embedding
model and domain.

---

## Verification Checklist

After deploying changes:

```cypher
-- Phase 1: Temporal fields present
MATCH (n:Concept) RETURN n.name, n.created_at, n.access_count LIMIT 5

-- Phase 2: Composite scoring active (check /api/search vs. raw cosine)
-- POST /api/chat with a message, then GET /api/communities

-- Phase 3: Lifecycle endpoints
-- GET /api/memory/health
-- POST /api/memory/prune (dry_run=true)

-- Phase 4: Graph visualization
-- Open the graph panel and switch to graph view

-- Phase 5: Eval harness
-- python -m eval.harness
```
