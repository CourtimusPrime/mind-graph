# Mind-Graph Plugin System

The plugin system allows you to replace any of the four core subsystems with a custom implementation, without modifying the framework internals.

---

## Available Plugin Interfaces

All interfaces are defined in `backend/plugins/base.py` as Python `Protocol` classes.

| Protocol | Default | Replaces |
|----------|---------|----------|
| `EmbedPlugin` | `ollama/nomic-embed-text` | Embedding backend |
| `ExtractorPlugin` | `openrouter/gpt-4o-mini` | Entity extraction |
| `RetrievalPlugin` | Composite formula | Retrieval scoring |
| `LifecyclePlugin` | Built-in fitness | Pruning fitness |

---

## Loading a Plugin

Reference a plugin in `.env` by its full dotted import path:

```env
EMBED_PLUGIN=mypackage.embeddings.MyEmbedder
EXTRACTOR_PLUGIN=mypackage.extractor.MyExtractor
```

Then in your startup code:

```python
from backend.plugins.base import load_plugin, EmbedPlugin

embed_cls = load_plugin(os.getenv("EMBED_PLUGIN", ""))
embed_instance = embed_cls()
assert isinstance(embed_instance, EmbedPlugin)  # runtime check
```

---

## EmbedPlugin

```python
class EmbedPlugin(Protocol):
    async def embed(self, text: str) -> list[float]: ...
```

**Requirements:**
- Must return a list of floats with exactly `EMBEDDING_DIM` dimensions (default: 768).
- Changing dimensionality requires dropping and recreating all vector indexes and re-embedding all nodes.
- Must be unit-normalized (cosine similarity requires it).

**Example — OpenAI embeddings:**

```python
import openai

class OpenAIEmbedder:
    def __init__(self):
        self.client = openai.AsyncOpenAI()

    async def embed(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding
```

**Note:** OpenAI `text-embedding-3-small` returns 1536 dimensions by default. Set `EMBEDDING_DIM=1536` and recreate indexes.

---

## ExtractorPlugin

```python
class ExtractorPlugin(Protocol):
    async def extract(self, text: str, context: dict) -> dict: ...
```

**Requirements:**
- Must return `{"nodes": [...], "relationships": [...]}`.
- `context` includes `{"project_hint": str | None}`.
- Node format: `{"name": str, "type": "Concept|Project|Note|Tag", "content": str}`.
- Relationship format: `{"source": str, "target": str, "type": "UPPER_SNAKE_CASE"}`.
- Return `{"nodes": [], "relationships": []}` on failure — never raise.

**Example — local spaCy NER:**

```python
import spacy

class SpacyExtractor:
    def __init__(self):
        self.nlp = spacy.load("en_core_web_trf")

    async def extract(self, text: str, context: dict) -> dict:
        doc = self.nlp(text)
        nodes = [
            {"name": ent.text, "type": "Concept", "content": ""}
            for ent in doc.ents
        ]
        return {"nodes": nodes, "relationships": []}
```

---

## RetrievalPlugin

```python
class RetrievalPlugin(Protocol):
    async def score(self, query_embedding: list[float], node: dict) -> float: ...
```

**Requirements:**
- Return a float in [0, 1].
- Called after vector search returns candidates.
- `node` dict includes all Neo4j properties plus `_score` (cosine) and `_label`.

**Example — recency-boosted scorer:**

```python
from datetime import datetime

class RecencyBooster:
    async def score(self, query_embedding: list[float], node: dict) -> float:
        base = node.get("_score", 0.0)
        created = node.get("created_at")
        if not created:
            return base
        age_days = (datetime.utcnow() - datetime.fromisoformat(created)).days
        recency = max(0.0, 1.0 - age_days / 90)
        return min(1.0, base * 0.7 + recency * 0.3)
```

---

## LifecyclePlugin

```python
class LifecyclePlugin(Protocol):
    async def fitness(self, node: dict, graph_context: dict) -> float: ...
```

**Requirements:**
- Return a float; higher = more valuable to keep.
- `graph_context` provides `{"degree": int, "age_days": float}`.
- Nodes below the configured `min_fitness` threshold (default: 1.0) are pruning candidates after `min_age_days`.

**Example — access-only fitness:**

```python
class AccessOnlyFitness:
    async def fitness(self, node: dict, graph_context: dict) -> float:
        return float(node.get("access_count", 0)) * 2.0
```

---

## Plugin Discovery

Plugins are loaded via `importlib` at runtime. The module must be importable (on `PYTHONPATH`). For local plugins, add them to the `backend/` directory or install via `pip install -e .`.

```python
from backend.plugins.base import load_plugin

cls = load_plugin("mypackage.embeddings.MyEmbedder")
instance = cls()
```

The `load_plugin` function is a thin wrapper over `importlib.import_module` — no magic, fully debuggable.
