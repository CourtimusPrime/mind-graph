from typing import Callable, Awaitable
from neo4j import AsyncGraphDatabase, AsyncDriver
from backend.config import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    EMBEDDING_DIM, DEDUP_THRESHOLD,
)

# Labels that have vector indexes
_VECTOR_LABELS = ["Concept", "Project", "Note", "Tag"]


class Neo4jClient:
    def __init__(self):
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )

    async def close(self):
        await self._driver.close()

    async def clear_all_data(self) -> None:
        """Delete all nodes and relationships from the graph."""
        async with self._driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")

    async def delete_node(self, label: str, name: str) -> int:
        """
        Delete a node by label and name, then delete any neighbours that
        become orphans (no remaining connections) after the deletion.
        Returns the total number of nodes deleted.
        """
        async with self._driver.session() as session:
            # Collect neighbours that will become orphans once this node is gone.
            # A neighbour is an orphan if all its connections lead only to this node.
            result = await session.run(
                """
                MATCH (n) WHERE $label IN labels(n) AND n.name = $name
                OPTIONAL MATCH (n)-[]-(neighbor)
                WITH n, collect(DISTINCT neighbor) AS neighbors
                UNWIND neighbors AS neighbor
                OPTIONAL MATCH (neighbor)-[]-(other) WHERE other <> n
                WITH n, neighbor, count(other) AS other_connections
                WITH n, collect(CASE WHEN other_connections = 0 THEN neighbor ELSE null END) AS orphans
                DETACH DELETE n
                WITH orphans
                UNWIND orphans AS orphan
                DETACH DELETE orphan
                RETURN size(orphans) + 1 AS deleted_count
                """,
                label=label,
                name=name,
            )
            record = await result.single()
            return record["deleted_count"] if record else 1

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def _init_schema(self):
        """Create full-text and vector indexes if they don't already exist."""
        async with self._driver.session() as session:
            # Full-text index across all node name/content properties
            await session.run(
                """
                CREATE FULLTEXT INDEX nodeSearch IF NOT EXISTS
                FOR (n:Concept|Project|Note|Tag)
                ON EACH [n.name, n.content]
                """
            )

            # One vector index per label
            for label in _VECTOR_LABELS:
                await session.run(
                    f"""
                    CREATE VECTOR INDEX {label.lower()}Embeddings IF NOT EXISTS
                    FOR (n:{label})
                    ON (n.embedding)
                    OPTIONS {{
                        indexConfig: {{
                            `vector.dimensions`: {EMBEDDING_DIM},
                            `vector.similarity_function`: 'cosine'
                        }}
                    }}
                    """
                )

    # ------------------------------------------------------------------
    # Vector search helpers
    # ------------------------------------------------------------------

    async def find_similar_node(
        self,
        label: str,
        embedding: list[float],
        threshold: float = DEDUP_THRESHOLD,
    ) -> dict | None:
        """
        Search a single label's vector index and return the top hit if its
        similarity score meets *threshold*, otherwise return None.
        """
        index_name = f"{label.lower()}Embeddings"
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                CALL db.index.vector.queryNodes($index, 1, $embedding)
                YIELD node, score
                WHERE score >= $threshold
                RETURN node, score
                LIMIT 1
                """,
                index=index_name,
                embedding=embedding,
                threshold=threshold,
            )
            record = await result.single()
            if record is None:
                return None
            node = dict(record["node"])
            node["_score"] = record["score"]
            node["_label"] = label
            return node

    async def vector_search(
        self,
        embedding: list[float],
        top_k: int = 8,
    ) -> list[dict]:
        """
        Query all four vector indexes, merge results, dedup by name, and
        return the top *top_k* hits sorted by descending score.
        """
        hits: list[dict] = []
        seen_names: set[str] = set()

        for label in _VECTOR_LABELS:
            index_name = f"{label.lower()}Embeddings"
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    CALL db.index.vector.queryNodes($index, $k, $embedding)
                    YIELD node, score
                    RETURN node, score
                    """,
                    index=index_name,
                    k=top_k,
                    embedding=embedding,
                )
                async for record in result:
                    node = dict(record["node"])
                    name = node.get("name", "")
                    if name and name not in seen_names:
                        seen_names.add(name)
                        node["_score"] = record["score"]
                        node["_label"] = label
                        hits.append(node)

        hits.sort(key=lambda n: n["_score"], reverse=True)
        return hits[:top_k]

    # ------------------------------------------------------------------
    # Graph expansion
    # ------------------------------------------------------------------

    async def expand_from_nodes(self, names: list[str]) -> list[dict]:
        """
        Return 1-hop neighbours for each node in *names*, including the
        relationship type connecting them.
        """
        if not names:
            return []

        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n)-[r]-(neighbour)
                WHERE n.name IN $names
                RETURN n.name AS source, type(r) AS rel_type,
                       neighbour.name AS neighbour_name,
                       labels(neighbour)[0] AS neighbour_label,
                       neighbour.content AS neighbour_content
                """,
                names=names,
            )
            rows = []
            async for record in result:
                rows.append(dict(record))
            return rows

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    async def get_session_project(self, session_id: str) -> str | None:
        """Return the name of the most recently created Project for this session."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:Project {session_id: $sid})
                RETURN n.name AS name
                ORDER BY n.name
                LIMIT 1
                """,
                sid=session_id,
            )
            record = await result.single()
            return record["name"] if record else None

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    async def upsert_entities(
        self,
        entities: dict,
        session_id: str,
        embed_fn: Callable[[str], Awaitable[list[float]]],
    ):
        """
        Merge nodes and relationships extracted by the LLM into the graph.
        Deduplicates semantically similar nodes before creating new ones.
        """
        nodes: list[dict] = entities.get("nodes", [])
        relationships: list[dict] = entities.get("relationships", [])

        # Map from the extractor-assigned name to the canonical graph name
        name_map: dict[str, str] = {}

        for node in nodes:
            label = node.get("type", "Concept")
            name = node.get("name", "").strip()
            if not name:
                continue

            content = node.get("content", "")
            embed_text = f"{name}: {content}" if content else name
            embedding = await embed_fn(embed_text)
            existing = await self.find_similar_node(label, embedding)

            if existing:
                # Reuse the existing node's name so relationships wire up correctly
                canonical = existing.get("name", name)
                name_map[name] = canonical
                # Optionally update the embedding if it was missing
                if not existing.get("embedding"):
                    async with self._driver.session() as session:
                        await session.run(
                            f"""
                            MATCH (n:{label} {{name: $name}})
                            SET n.embedding = $embedding
                            """,
                            name=canonical,
                            embedding=embedding,
                        )
            else:
                name_map[name] = name
                async with self._driver.session() as session:
                    await session.run(
                        f"""
                        MERGE (n:{label} {{name: $name}})
                        ON CREATE SET n.embedding = $embedding,
                                      n.session_id = $session_id,
                                      n.content = $content
                        ON MATCH  SET n.embedding = $embedding,
                                      n.content = CASE WHEN $content <> '' THEN $content ELSE n.content END
                        """,
                        name=name,
                        embedding=embedding,
                        session_id=session_id,
                        content=content,
                    )

        # Create relationships using canonical names
        for rel in relationships:
            src = name_map.get(rel.get("source", ""), rel.get("source", ""))
            tgt = name_map.get(rel.get("target", ""), rel.get("target", ""))
            rel_type = rel.get("type", "RELATED_TO").upper().replace(" ", "_")
            if not src or not tgt:
                continue
            async with self._driver.session() as session:
                await session.run(
                    f"""
                    MATCH (a {{name: $src}}), (b {{name: $tgt}})
                    MERGE (a)-[:{rel_type}]->(b)
                    """,
                    src=src,
                    tgt=tgt,
                )
