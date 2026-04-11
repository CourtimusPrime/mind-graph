"""
Mind-Graph Plugin Interface

Defines the four Protocol classes that make the memory system extensible.
Any class satisfying a Protocol can be loaded as a drop-in replacement for
the corresponding subsystem.

Plugin loading is config-driven:
    EMBED_PLUGIN=mypackage.mymodule.MyEmbedder
    EXTRACTOR_PLUGIN=mypackage.mymodule.MyExtractor
    RETRIEVAL_PLUGIN=mypackage.mymodule.MyScorer
    LIFECYCLE_PLUGIN=mypackage.mymodule.MyLifecycle

See docs/PLUGINS.md for the complete authoring guide.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedPlugin(Protocol):
    """
    Replaces the default Ollama nomic-embed-text backend.

    The returned vector must have exactly EMBEDDING_DIM dimensions
    (default: 768). Changing the dimensionality requires re-indexing
    all existing nodes.
    """

    async def embed(self, text: str) -> list[float]:
        """Embed *text* and return a unit-normalized float vector."""
        ...


@runtime_checkable
class ExtractorPlugin(Protocol):
    """
    Replaces the default OpenRouter JSON-mode entity extractor.

    Must return a dict matching the schema:
        {
          "nodes": [{"name": str, "type": str, "content": str}, ...],
          "relationships": [{"source": str, "target": str, "type": str}, ...]
        }
    """

    async def extract(self, text: str, context: dict) -> dict:
        """
        Extract entities and relationships from *text*.
        *context* may include {"project_hint": str | None}.
        """
        ...


@runtime_checkable
class RetrievalPlugin(Protocol):
    """
    Supplements or replaces the composite retrieval scorer.

    The score is blended with semantic and centrality components.
    Return a float in [0, 1].
    """

    async def score(self, query_embedding: list[float], node: dict) -> float:
        """Score *node*'s relevance to a query represented by *query_embedding*."""
        ...


@runtime_checkable
class LifecyclePlugin(Protocol):
    """
    Replaces or augments the default fitness formula used for pruning.

    Higher fitness = more valuable to keep. The default formula is:
        (access_count × 2.0) + (log(degree+1) × 1.5) + (exp(-0.693×age/30) × 3.0)

    Return a float; nodes below the configured threshold become pruning candidates.
    """

    async def fitness(self, node: dict, graph_context: dict) -> float:
        """
        Compute a fitness score for *node*.
        *graph_context* provides {"degree": int, "age_days": float}.
        """
        ...


def load_plugin(dotted_path: str):
    """
    Load a plugin class from a dotted module path and return the class.

    Example:
        cls = load_plugin("mypackage.myplugin.MyEmbedder")
        instance = cls()
    """
    import importlib

    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
