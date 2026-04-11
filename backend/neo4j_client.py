from typing import Callable, Awaitable
from neo4j import AsyncGraphDatabase, AsyncDriver
from backend.config import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    EMBEDDING_DIM, DEDUP_THRESHOLD,
    RETRIEVAL_SEMANTIC_WEIGHT, RETRIEVAL_CENTRALITY_WEIGHT, RETRIEVAL_RECENCY_WEIGHT,
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
        """Create full-text, vector, and temporal indexes if they don't already exist."""
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

            # Per-label temporal indexes for lifecycle queries
            for label in _VECTOR_LABELS:
                await session.run(
                    f"""
                    CREATE INDEX node_session_time_{label.lower()} IF NOT EXISTS
                    FOR (n:{label}) ON (n.session_id, n.created_at)
                    """
                )

            # ExtractionError dedup index
            await session.run(
                """
                CREATE INDEX extraction_error_hash IF NOT EXISTS
                FOR (n:ExtractionError) ON (n.text_hash)
                """
            )

    # ------------------------------------------------------------------
    # Vector search
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
                """
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
        return the top *top_k* hits sorted by descending cosine score.
        Used by the /api/search route (raw similarity, no composite scoring).
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

    async def ranked_vector_search(
        self,
        embedding: list[float],
        top_k: int = 8,
    ) -> list[dict]:
        """
        Query all four vector indexes with composite scoring:
            composite = (semantic × W_s) + (centrality × W_c) + (recency × W_r)

        Semantic:   raw cosine similarity from vector index
        Centrality: log(degree + 1) / 10  — hub nodes score higher
        Recency:    exp(-0.693 × age_days / 7)  — 7-day half-life

        Used by RAG retrieval. Weights are configurable via env vars.
        """
        hits: list[dict] = []
        seen_names: set[str] = set()

        for label in _VECTOR_LABELS:
            index_name = f"{label.lower()}Embeddings"
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    CALL db.index.vector.queryNodes($index, $k, $embedding)
                    YIELD node, score AS vector_score
                    OPTIONAL MATCH (node)-[r]-()
                    WITH node, vector_score, count(r) AS degree
                    WITH node, vector_score, degree,
                         exp(-0.693 * toFloat(
                             datetime().epochMillis
                             - coalesce(node.created_at, datetime()).epochMillis
                         ) / 86400000.0 / 7.0) AS recency_score
                    WITH node,
                         (vector_score * $semantic_w)
                         + (log(degree + 1) / 10.0 * $centrality_w)
                         + (recency_score * $recency_w) AS composite_score
                    ORDER BY composite_score DESC
                    RETURN node, composite_score
                    LIMIT $k
                    """,
                    index=index_name,
                    k=top_k,
                    embedding=embedding,
                    semantic_w=RETRIEVAL_SEMANTIC_WEIGHT,
                    centrality_w=RETRIEVAL_CENTRALITY_WEIGHT,
                    recency_w=RETRIEVAL_RECENCY_WEIGHT,
                )
                async for record in result:
                    node = dict(record["node"])
                    name = node.get("name", "")
                    if name and name not in seen_names:
                        seen_names.add(name)
                        node["_score"] = record["composite_score"]
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
        relationship type connecting them and the neighbour's content.
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
    # Access recording (Law 2: every retrieval updates provenance)
    # ------------------------------------------------------------------

    async def record_access(self, names: list[str]) -> None:
        """Increment access_count and update last_accessed for retrieved nodes."""
        if not names:
            return
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (n) WHERE n.name IN $names
                SET n.access_count = coalesce(n.access_count, 0) + 1,
                    n.last_accessed = datetime()
                """,
                names=names,
            )

    # ------------------------------------------------------------------
    # Extraction error logging
    # ------------------------------------------------------------------

    async def log_extraction_error(
        self, session_id: str, text_hash: str, error: str
    ) -> None:
        """Create or update an ExtractionError node tracking repeated failures."""
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (e:ExtractionError {text_hash: $text_hash})
                ON CREATE SET e.session_id = $session_id,
                              e.error = $error,
                              e.created_at = datetime(),
                              e.retry_count = 1
                ON MATCH  SET e.error = $error,
                              e.last_seen = datetime(),
                              e.retry_count = coalesce(e.retry_count, 0) + 1
                """,
                text_hash=text_hash,
                session_id=session_id,
                error=error,
            )

    # ------------------------------------------------------------------
    # Upsert (Law 2: provenance on every node)
    # ------------------------------------------------------------------

    async def upsert_entities(
        self,
        entities: dict,
        session_id: str,
        embed_fn: Callable[[str], Awaitable[list[float]]],
    ) -> int:
        """
        Merge nodes and relationships extracted by the LLM into the graph.
        Deduplicates semantically similar nodes before creating new ones.

        ON CREATE: sets embedding, session_id, content, created_at, updated_at, access_count=0
        ON MATCH:  increments access_count, refreshes updated_at

        Relationships get created_at, session_id, weight on create;
        weight increments by 0.1 on each subsequent observation.

        Returns the count of nodes that went through the MERGE path (new or renamed).
        """
        nodes: list[dict] = entities.get("nodes", [])
        relationships: list[dict] = entities.get("relationships", [])
        name_map: dict[str, str] = {}
        new_count = 0

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
                # Reuse the canonical name so relationships wire up correctly.
                canonical = existing.get("name", name)
                name_map[name] = canonical
                async with self._driver.session() as session:
                    if not existing.get("embedding"):
                        await session.run(
                            f"""
                            MATCH (n:{label} {{name: $name}})
                            SET n.embedding = $embedding,
                                n.updated_at = datetime(),
                                n.access_count = coalesce(n.access_count, 0) + 1
                            """,
                            name=canonical,
                            embedding=embedding,
                        )
                    else:
                        await session.run(
                            f"""
                            MATCH (n:{label} {{name: $name}})
                            SET n.updated_at = datetime(),
                                n.access_count = coalesce(n.access_count, 0) + 1
                            """,
                            name=canonical,
                        )
            else:
                name_map[name] = name
                new_count += 1
                async with self._driver.session() as session:
                    await session.run(
                        f"""
                        MERGE (n:{label} {{name: $name}})
                        ON CREATE SET n.embedding = $embedding,
                                      n.session_id = $session_id,
                                      n.content = $content,
                                      n.created_at = datetime(),
                                      n.updated_at = datetime(),
                                      n.access_count = 0
                        ON MATCH  SET n.embedding = CASE WHEN $content <> '' THEN $embedding
                                                         ELSE n.embedding END,
                                      n.content    = CASE WHEN $content <> '' THEN $content
                                                         ELSE n.content END,
                                      n.updated_at = datetime(),
                                      n.access_count = coalesce(n.access_count, 0) + 1
                        """,
                        name=name,
                        embedding=embedding,
                        session_id=session_id,
                        content=content,
                    )

        # Create relationships using canonical names.
        # Relationship weight grows with each observation, making reinforced
        # connections more prominent in the graph visualization.
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
                    MERGE (a)-[r:{rel_type}]->(b)
                    ON CREATE SET r.created_at = datetime(),
                                  r.session_id = $session_id,
                                  r.weight = 1.0
                    ON MATCH  SET r.weight = coalesce(r.weight, 1.0) + 0.1,
                                  r.last_seen = datetime()
                    """,
                    src=src,
                    tgt=tgt,
                    session_id=session_id,
                )

        return new_count

    # ------------------------------------------------------------------
    # Graph data for visualization (Phase 4)
    # ------------------------------------------------------------------

    async def get_graph_data(self) -> dict:
        """
        Return all nodes and edges suitable for the force-directed graph view.
        Excludes ExtractionError, Community nodes, and consolidated Notes.
        """
        async with self._driver.session() as session:
            node_result = await session.run(
                """
                MATCH (n)
                WHERE n.name IS NOT NULL
                  AND NOT n:ExtractionError
                  AND NOT n:Community
                  AND NOT coalesce(n.consolidated, false)
                RETURN elementId(n) AS id,
                       labels(n)[0] AS label,
                       n.name AS name,
                       n.content AS content,
                       coalesce(n.access_count, 0) AS access_count,
                       toString(n.created_at) AS created_at
                ORDER BY label, name
                """
            )
            nodes = []
            async for record in node_result:
                nodes.append(dict(record))

            edge_result = await session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE a.name IS NOT NULL AND b.name IS NOT NULL
                  AND NOT a:ExtractionError AND NOT b:ExtractionError
                  AND NOT a:Community AND NOT b:Community
                  AND type(r) <> 'BELONGS_TO_COMMUNITY'
                  AND type(r) <> 'CONSOLIDATED_INTO'
                RETURN elementId(a) AS source_id,
                       elementId(b) AS target_id,
                       type(r) AS type,
                       coalesce(r.weight, 1.0) AS weight
                """
            )
            edges = []
            async for record in edge_result:
                edges.append(dict(record))

        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # Community detection support (Phase 2)
    # ------------------------------------------------------------------

    async def get_edge_list(self) -> list[tuple[str, str, float]]:
        """Return all edges as (source_name, target_name, weight) for community detection."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a)-[r]-(b)
                WHERE a.name IS NOT NULL AND b.name IS NOT NULL
                  AND NOT a:ExtractionError AND NOT b:ExtractionError
                  AND type(r) <> 'BELONGS_TO_COMMUNITY'
                RETURN a.name AS source, b.name AS target,
                       coalesce(r.weight, 1.0) AS weight
                """
            )
            edges = []
            async for record in result:
                edges.append((record["source"], record["target"], record["weight"]))
            return edges

    async def get_node_embeddings(self, label: str | None = None) -> list[dict]:
        """Return nodes with their embeddings for community centroid computation."""
        label_filter = f"AND n:{label}" if label else ""
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (n)
                WHERE n.name IS NOT NULL AND n.embedding IS NOT NULL
                  AND NOT n:ExtractionError AND NOT n:Community
                  {label_filter}
                RETURN n.name AS name, labels(n)[0] AS label, n.embedding AS embedding
                """
            )
            nodes = []
            async for record in result:
                nodes.append(dict(record))
            return nodes

    async def upsert_community(
        self,
        community_id: str,
        members: list[str],
        level: int,
        centroid: list[float],
    ) -> None:
        """Create or update a Community node and link members to it."""
        async with self._driver.session() as session:
            # Use a non-empty centroid if provided; don't overwrite with []
            if centroid:
                await session.run(
                    """
                    MERGE (c:Community {id: $id})
                    SET c.members = $members,
                        c.level = $level,
                        c.centroid = $centroid,
                        c.size = size($members),
                        c.updated_at = datetime()
                    """,
                    id=community_id,
                    members=members,
                    level=level,
                    centroid=centroid,
                )
            else:
                await session.run(
                    """
                    MERGE (c:Community {id: $id})
                    SET c.members = $members,
                        c.level = $level,
                        c.size = size($members),
                        c.updated_at = datetime()
                    """,
                    id=community_id,
                    members=members,
                    level=level,
                )
            # Link each member to the community
            for member_name in members:
                await session.run(
                    """
                    MATCH (n {name: $name}), (c:Community {id: $id})
                    MERGE (n)-[:BELONGS_TO_COMMUNITY]->(c)
                    """,
                    name=member_name,
                    id=community_id,
                )

    async def set_community_label(self, community_id: str, label: str) -> None:
        """Set the LLM-generated human-readable label for a community."""
        async with self._driver.session() as session:
            await session.run(
                "MATCH (c:Community {id: $id}) SET c.label = $label",
                id=community_id,
                label=label,
            )

    async def get_communities(self) -> list[dict]:
        """Return all communities with their members and LLM-generated labels."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Community)
                RETURN c.id AS id, c.members AS members, c.size AS size,
                       c.label AS label, c.level AS level,
                       toString(c.updated_at) AS updated_at
                ORDER BY c.size DESC
                """
            )
            communities = []
            async for record in result:
                communities.append(dict(record))
            return communities

    # ------------------------------------------------------------------
    # Memory lifecycle: consolidation candidates (Phase 3)
    # ------------------------------------------------------------------

    async def find_consolidation_candidates(
        self,
        low: float = 0.80,
        high: float = 0.91,
    ) -> list[list[str]]:
        """
        Find clusters of Note nodes with pairwise similarity in [low, high).
        Uses the vector index per note as a probe — not O(n²) full Cypher.
        Returns list of clusters (each cluster is a list of node names).
        Clusters must have 3+ members to be actionable.
        """
        # Fetch all non-consolidated Note embeddings
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:Note)
                WHERE n.embedding IS NOT NULL AND NOT coalesce(n.consolidated, false)
                RETURN n.name AS name, n.embedding AS embedding
                """
            )
            notes = [{"name": r["name"], "embedding": r["embedding"]} async for r in result]

        if len(notes) < 3:
            return []

        # Build adjacency: note → set of similar notes in the band
        adjacency: dict[str, set[str]] = {n["name"]: set() for n in notes}
        for note in notes:
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    CALL db.index.vector.queryNodes('noteEmbeddings', 10, $embedding)
                    YIELD node, score
                    WHERE score >= $low AND score < $high AND node.name <> $self
                    RETURN node.name AS name
                    """,
                    embedding=note["embedding"],
                    low=low,
                    high=high,
                    self=note["name"],
                )
                async for record in result:
                    neighbor = record["name"]
                    adjacency[note["name"]].add(neighbor)
                    if neighbor in adjacency:
                        adjacency[neighbor].add(note["name"])

        # Find connected components via BFS
        visited: set[str] = set()
        clusters: list[list[str]] = []
        for name in adjacency:
            if name in visited or not adjacency[name]:
                continue
            cluster: list[str] = []
            queue = [name]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                cluster.append(current)
                for neighbor in adjacency.get(current, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            if len(cluster) >= 3:
                clusters.append(cluster)

        return clusters

    async def create_concept_from_notes(
        self,
        note_names: list[str],
        summary: str,
        embed_fn: Callable[[str], Awaitable[list[float]]],
        session_id: str = "consolidation",
    ) -> str:
        """
        Create a Concept node that consolidates the given Note nodes.
        Links notes with CONSOLIDATED_INTO and marks them consolidated=true.
        Returns the new concept name.
        """
        concept_name = summary.split(".")[0].strip()[:80]
        embedding = await embed_fn(f"{concept_name}: {summary}")

        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (c:Concept {name: $name})
                ON CREATE SET c.embedding = $embedding,
                              c.content = $content,
                              c.session_id = $session_id,
                              c.created_at = datetime(),
                              c.updated_at = datetime(),
                              c.access_count = 0
                ON MATCH  SET c.content = $content, c.updated_at = datetime()
                """,
                name=concept_name,
                embedding=embedding,
                content=summary,
                session_id=session_id,
            )
            for note_name in note_names:
                await session.run(
                    """
                    MATCH (n:Note {name: $note_name}), (c:Concept {name: $concept_name})
                    MERGE (n)-[:CONSOLIDATED_INTO]->(c)
                    SET n.consolidated = true
                    """,
                    note_name=note_name,
                    concept_name=concept_name,
                )

        return concept_name

    # ------------------------------------------------------------------
    # Memory lifecycle: fitness and pruning (Phase 3)
    # ------------------------------------------------------------------

    async def find_low_fitness_nodes(
        self,
        min_fitness: float = 1.0,
        min_age_days: int = 14,
    ) -> list[dict]:
        """
        Find nodes whose fitness score is below *min_fitness* and are at least
        *min_age_days* old. Never touches ExtractionError or Community nodes.

        Fitness formula:
            (access_count × 2.0)          — reinforcement signal
          + (log(degree + 1) × 1.5)       — connectivity bonus
          + (exp(-0.693 × age/30) × 3.0)  — recency, 30-day half-life
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n)
                WHERE n.name IS NOT NULL
                  AND NOT n:ExtractionError
                  AND NOT n:Community
                OPTIONAL MATCH (n)-[r]-()
                WITH n, count(r) AS degree,
                     toFloat(datetime().epochMillis
                         - coalesce(n.created_at, datetime()).epochMillis)
                         / 86400000.0 AS age_days
                WITH n, degree, age_days,
                     (coalesce(n.access_count, 0) * 2.0)
                     + (log(degree + 1) * 1.5)
                     + (exp(-0.693 * age_days / 30.0) * 3.0) AS fitness
                WHERE fitness < $min_fitness AND age_days >= $min_age_days
                RETURN labels(n)[0] AS label, n.name AS name,
                       fitness, age_days, degree,
                       coalesce(n.access_count, 0) AS access_count
                ORDER BY fitness ASC
                """,
                min_fitness=min_fitness,
                min_age_days=min_age_days,
            )
            return [dict(r) async for r in result]

    async def has_active_project_connection(self, name: str) -> bool:
        """Check if a node is connected within 2 hops to any Project (protected from pruning)."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n {name: $name})-[*1..2]-(p:Project)
                RETURN count(p) > 0 AS has_project
                LIMIT 1
                """,
                name=name,
            )
            record = await result.single()
            return bool(record["has_project"]) if record else False

    async def get_memory_health(self) -> dict:
        """Return per-type node statistics for the /api/memory/health endpoint."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n)
                WHERE n.name IS NOT NULL
                  AND NOT n:ExtractionError
                  AND NOT n:Community
                OPTIONAL MATCH (n)-[r]-()
                WITH labels(n)[0] AS label,
                     coalesce(n.access_count, 0) AS ac,
                     count(r) AS degree
                RETURN label,
                       count(*) AS node_count,
                       avg(toFloat(ac)) AS avg_access,
                       avg(toFloat(degree)) AS avg_centrality
                ORDER BY label
                """
            )
            stats = [dict(r) async for r in result]
        return {"by_type": stats}
