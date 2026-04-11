from typing import Callable, Awaitable
from backend.neo4j_client import Neo4jClient
from backend.config import RAG_CONTEXT_CHARS


class GraphRAG:
    def __init__(self, db: Neo4jClient):
        self._db = db

    async def get_context(
        self,
        user_message: str,
        embed_fn: Callable[[str], Awaitable[list[float]]],
    ) -> str:
        """
        Build a context string relevant to *user_message* using composite-scored
        vector search (semantic × centrality × recency) + 1-hop expansion.

        Direct hits are ranked by composite score; neighbours fill remaining budget.
        Total character budget is configurable via RAG_CONTEXT_CHARS.
        """
        embedding = await embed_fn(user_message)

        direct_hits = await self._db.ranked_vector_search(embedding, top_k=8)
        if not direct_hits:
            return ""

        hit_names = [h.get("name", "") for h in direct_hits if h.get("name")]
        # Record access so retrieval frequency is reflected in future fitness scores
        await self._db.record_access(hit_names)

        neighbours = await self._db.expand_from_nodes(hit_names)

        # Build direct-hit lines ordered by composite score (already sorted)
        direct_lines: list[tuple[float, str]] = []
        for node in direct_hits:
            name = node.get("name", "")
            label = node.get("_label", "")
            content = node.get("content", "")
            score = node.get("_score", 0.0)
            entry = f"  [{label}] {name}"
            if content:
                entry += f": {content}"
            direct_lines.append((score, entry))

        # Neighbour lines (added after direct hits, lower priority)
        neighbour_lines: list[str] = []
        for row in neighbours:
            neighbour_content = row.get("neighbour_content") or ""
            detail = f": {neighbour_content}" if neighbour_content else ""
            neighbour_lines.append(
                f"  {row['source']} --[{row['rel_type']}]--> "
                f"[{row['neighbour_label']}] {row['neighbour_name']}{detail}"
            )

        # Greedy fill: consume budget from highest-score lines down
        header = "Relevant knowledge graph nodes:"
        lines: list[str] = [header]
        budget = RAG_CONTEXT_CHARS - len(header) - 1

        for _, line in sorted(direct_lines, key=lambda x: x[0], reverse=True):
            if budget <= 0:
                break
            if len(line) <= budget:
                lines.append(line)
                budget -= len(line) + 1  # +1 for the newline

        if budget > 0 and neighbour_lines:
            sep = "\nNeighbours:"
            if len(sep) <= budget:
                lines.append(sep)
                budget -= len(sep) + 1
                for line in neighbour_lines:
                    if budget <= 0:
                        break
                    if len(line) <= budget:
                        lines.append(line)
                        budget -= len(line) + 1

        return "\n".join(lines)
