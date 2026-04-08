from typing import Callable, Awaitable
from backend.neo4j_client import Neo4jClient

_MAX_CONTEXT_CHARS = 1500


class GraphRAG:
    def __init__(self, db: Neo4jClient):
        self._db = db

    async def get_context(
        self,
        user_message: str,
        embed_fn: Callable[[str], Awaitable[list[float]]],
    ) -> str:
        """
        Build a context string from the knowledge graph relevant to
        *user_message* using vector similarity search + 1-hop expansion.

        Returns a string of at most _MAX_CONTEXT_CHARS characters.
        """
        embedding = await embed_fn(user_message)

        direct_hits = await self._db.vector_search(embedding, top_k=6)
        if not direct_hits:
            return ""

        hit_names = [h.get("name", "") for h in direct_hits if h.get("name")]
        neighbours = await self._db.expand_from_nodes(hit_names)

        # --- Format direct hits ---
        lines: list[str] = ["Relevant knowledge graph nodes:"]
        for node in direct_hits:
            name = node.get("name", "")
            label = node.get("_label", "")
            content = node.get("content", "")
            entry = f"  [{label}] {name}"
            if content:
                entry += f": {content}"
            lines.append(entry)

        direct_block = "\n".join(lines)

        # --- Format neighbours (truncated first if needed) ---
        neighbour_lines: list[str] = []
        if neighbours:
            neighbour_lines.append("\nNeighbours:")
            for row in neighbours:
                neighbour_lines.append(
                    f"  {row['source']} --[{row['rel_type']}]--> "
                    f"{row['neighbour_name']} ({row['neighbour_label']})"
                )

        # Combine and enforce character cap, truncating neighbours first
        full_context = direct_block + "\n".join(neighbour_lines)
        if len(full_context) <= _MAX_CONTEXT_CHARS:
            return full_context

        # Try without neighbours
        if len(direct_block) <= _MAX_CONTEXT_CHARS:
            return direct_block

        # Hard-truncate direct block
        return direct_block[:_MAX_CONTEXT_CHARS]
