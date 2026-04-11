"""
Community detection for the mind-graph knowledge store.

Uses the Louvain algorithm (via networkx) to find clusters of closely related
nodes, writes Community nodes back to Neo4j, and triggers async LLM labeling
for communities with 3+ members.

This module is loaded lazily — imported only when community detection is
triggered, so networkx is not required for the core server to start.
"""

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.neo4j_client import Neo4jClient


async def detect_communities(db: "Neo4jClient") -> None:
    """
    Run Louvain community detection on the current graph and write results to Neo4j.

    Algorithm:
      1. Pull full weighted edge list from Neo4j
      2. Build an undirected networkx graph
      3. Run louvain_communities() with edge weights
      4. Compute centroid embedding per community (mean of member embeddings)
      5. Write Community nodes and BELONGS_TO_COMMUNITY edges back to Neo4j
      6. For communities with 3+ members, trigger async LLM label generation
    """
    try:
        import networkx as nx
        from networkx.algorithms import community as nx_community
    except ImportError:
        print("[communities] networkx not installed — run: pip install networkx")
        return

    try:
        edges = await db.get_edge_list()
        if not edges:
            return

        # Build weighted undirected graph
        G = nx.Graph()
        for src, tgt, weight in edges:
            if G.has_edge(src, tgt):
                G[src][tgt]["weight"] = G[src][tgt].get("weight", 1.0) + weight
            else:
                G.add_edge(src, tgt, weight=weight)

        if len(G.nodes()) < 3:
            return

        communities = nx_community.louvain_communities(G, weight="weight", seed=42)

        # Build embedding lookup for centroid computation
        all_nodes = await db.get_node_embeddings()
        embedding_map: dict[str, list[float]] = {
            n["name"]: n["embedding"]
            for n in all_nodes
            if n.get("embedding")
        }

        for i, comm in enumerate(communities):
            members = list(comm)
            if not members:
                continue

            member_embeddings = [
                embedding_map[m] for m in members if m in embedding_map
            ]
            if member_embeddings:
                dim = len(member_embeddings[0])
                centroid = [
                    sum(emb[j] for emb in member_embeddings) / len(member_embeddings)
                    for j in range(dim)
                ]
            else:
                centroid = []

            community_id = f"comm_{i}"
            await db.upsert_community(
                community_id=community_id,
                members=members,
                level=0,
                centroid=centroid,
            )

            if len(members) >= 3:
                asyncio.create_task(_label_community(db, community_id, members))

        print(f"[communities] detected {len(communities)} communities")

    except Exception as exc:
        print(f"[communities] error during detection: {exc}")


async def _label_community(
    db: "Neo4jClient", community_id: str, members: list[str]
) -> None:
    """Generate a human-readable label for a community using the LLM."""
    try:
        from backend import openrouter

        # Cap member list to keep the prompt concise
        sample = ", ".join(members[:20])
        messages = [
            {
                "role": "user",
                "content": (
                    "These concepts form a cluster in a knowledge graph. "
                    "Summarize the theme that connects them in 1-2 sentences.\n\n"
                    f"Concepts: {sample}"
                ),
            }
        ]
        label = (await openrouter.chat(messages, context="")).strip()[:200]
        await db.set_community_label(community_id, label)
        print(f"[communities] labeled {community_id}: {label[:60]}…")

    except Exception as exc:
        print(f"[communities] labeling error for {community_id}: {exc}")
