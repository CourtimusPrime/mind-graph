"""
Memory pruning pipeline.

Identifies nodes with low fitness scores that are old enough to be considered
stale. Nodes connected to active Projects are never pruned (they may be dormant
rather than forgotten).

By default, dry_run=True — candidates are reported without deletion.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.neo4j_client import Neo4jClient


async def run_pruning(
    db: "Neo4jClient",
    dry_run: bool = True,
    min_fitness: float = 1.0,
    min_age_days: int = 14,
) -> dict:
    """
    Find and optionally delete low-fitness, stale nodes.

    Safety rules:
      - Nodes connected to any Project within 2 hops are skipped
      - Nodes younger than *min_age_days* are never touched
      - dry_run=True reports candidates without deleting anything

    Fitness formula (computed in Cypher):
        (access_count × 2.0)          # reinforcement signal
      + (log(degree + 1) × 1.5)       # connectivity bonus
      + (exp(-0.693 × age/30) × 3.0)  # recency, 30-day half-life

    Returns a report dict with candidate details.
    """
    try:
        candidates = await db.find_low_fitness_nodes(
            min_fitness=min_fitness,
            min_age_days=min_age_days,
        )

        deleted = 0
        project_protected = 0
        pruned: list[dict] = []

        for node in candidates:
            name = node.get("name", "")
            label = node.get("label", "")

            if await db.has_active_project_connection(name):
                project_protected += 1
                continue

            if not dry_run:
                await db.delete_node(label, name)
                deleted += 1
                print(
                    f"[pruning] deleted [{label}] '{name}' "
                    f"(fitness={node.get('fitness', 0):.2f}, "
                    f"age={node.get('age_days', 0):.0f}d)"
                )

            pruned.append(node)

        return {
            "dry_run": dry_run,
            "candidates_found": len(candidates),
            "project_protected": project_protected,
            "deleted": deleted,
            "candidates": pruned,
        }

    except Exception as exc:
        print(f"[pruning] error: {exc}")
        return {"error": str(exc)}
