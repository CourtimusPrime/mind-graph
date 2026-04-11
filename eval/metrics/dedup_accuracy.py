"""
Deduplication Accuracy metric.

For fixtures with dedup_check=true, sends semantically equivalent messages and
verifies that the graph does not create duplicate nodes for the same concept.

Score = fraction of dedup fixtures where no duplicates were created.
"""

import re
import httpx


async def measure_dedup_accuracy(
    client: httpx.AsyncClient,
    fixtures: list[dict],
    session_id: str,
) -> dict:
    dedup_fixtures = [f for f in fixtures if f.get("expected_entities", {}).get("dedup_check")]
    if not dedup_fixtures:
        return {"score": 1.0, "detail": "no dedup fixtures", "tested": 0}

    passed = 0
    details = []

    for fixture in dedup_fixtures:
        fid = fixture.get("id", "unknown")

        # Send all messages in the fixture
        for msg in fixture.get("messages", []):
            await client.post(
                "/api/chat",
                json={
                    "message": msg["content"],
                    "session_id": session_id,
                    "history": [],
                },
            )

        # Fetch all nodes
        res = await client.get("/api/nodes")
        if not res.is_success:
            details.append({"fixture": fid, "passed": False, "reason": f"nodes HTTP {res.status_code}"})
            continue

        nodes = res.json().get("nodes", [])

        # Check dedup_check in expected_graphs if available
        fixture_pass = True
        node_names = [n.get("name", "").lower() for n in nodes]

        for expected_node in fixture.get("expected_entities", {}).get("nodes", []):
            concept_name = expected_node.get("name", "").lower()
            # Count how many nodes match this concept name (allowing for minor variations)
            matching = [n for n in node_names if concept_name in n or n in concept_name]
            if len(matching) > 1:
                fixture_pass = False

        if fixture_pass:
            passed += 1

        details.append({
            "fixture": fid,
            "passed": fixture_pass,
            "total_nodes": len(nodes),
        })

    score = passed / len(dedup_fixtures) if dedup_fixtures else 1.0

    return {
        "score": round(score, 3),
        "passed": passed,
        "tested": len(dedup_fixtures),
        "detail": details,
    }
