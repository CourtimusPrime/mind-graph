"""
Memory consolidation pipeline.

Finds clusters of Note nodes with pairwise similarity in the 0.80–0.91 band
(related but below the 0.92 dedup threshold) and consolidates groups of 3+ into
a new Concept node via LLM summarization.

The original Notes are preserved (provenance) but marked consolidated=true
so they are filtered from the default UI listing.
"""

from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.neo4j_client import Neo4jClient


async def run_consolidation(
    db: "Neo4jClient",
    embed_fn: Callable[[str], Awaitable[list[float]]],
    low: float = 0.80,
    high: float = 0.91,
) -> dict:
    """
    Identify clusters of similar Notes and consolidate each cluster into a Concept.

    Returns a report dict with:
      - clusters_found: total clusters identified
      - concepts_created: number of Concepts written to the graph
      - details: list of {concept, consolidated_notes} per cluster
    """
    try:
        clusters = await db.find_consolidation_candidates(low=low, high=high)
        concepts_created = []

        for cluster in clusters:
            if len(cluster) < 3:
                continue

            from backend import openrouter

            sample = ", ".join(cluster[:10])
            messages = [
                {
                    "role": "user",
                    "content": (
                        "These notes share a common theme and should be consolidated "
                        "into a single concept. Write 1-2 sentences summarizing what "
                        "they collectively represent.\n\n"
                        f"Notes: {sample}"
                    ),
                }
            ]
            summary = (await openrouter.chat(messages, context="")).strip()

            concept_name = await db.create_concept_from_notes(
                note_names=cluster,
                summary=summary,
                embed_fn=embed_fn,
            )
            concepts_created.append(
                {"concept": concept_name, "consolidated_notes": cluster}
            )
            print(
                f"[consolidation] created concept '{concept_name}' "
                f"from {len(cluster)} notes"
            )

        return {
            "clusters_found": len(clusters),
            "concepts_created": len(concepts_created),
            "details": concepts_created,
        }

    except Exception as exc:
        print(f"[consolidation] error: {exc}")
        return {"error": str(exc)}
