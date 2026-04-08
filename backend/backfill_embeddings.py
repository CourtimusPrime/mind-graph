"""
CLI script to backfill embeddings for any nodes that are missing them.

Usage:
    cd /home/court/me/mind-graph
    python -m backend.backfill_embeddings
"""

import asyncio
from neo4j import AsyncGraphDatabase
from backend.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from backend.embeddings import embed


async def backfill():
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (n)
                WHERE n.name IS NOT NULL AND n.embedding IS NULL
                RETURN id(n) AS node_id, labels(n)[0] AS label, n.name AS name
                """
            )
            nodes = [dict(r) async for r in result]

        total = len(nodes)
        if total == 0:
            print("All nodes already have embeddings. Nothing to do.")
            return

        print(f"Found {total} node(s) without embeddings.")

        for i, node in enumerate(nodes, 1):
            name: str = node["name"]
            node_id: int = node["node_id"]

            try:
                embedding = await embed(name)
            except Exception as exc:
                print(f"  [SKIP] {name!r}: {exc}")
                continue

            async with driver.session() as session:
                await session.run(
                    """
                    MATCH (n)
                    WHERE id(n) = $node_id
                    SET n.embedding = $embedding
                    """,
                    node_id=node_id,
                    embedding=embedding,
                )

            if i % 10 == 0 or i == total:
                print(f"Embedded {i}/{total} nodes...")

        print("Done.")

    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(backfill())
