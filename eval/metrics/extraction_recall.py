"""
Extraction Recall metric.

For fixtures with expected_entities, sends messages and checks that the expected
nodes appear in the knowledge graph after extraction.

Score = fraction of expected nodes found across all fixtures.
"""

import httpx


async def measure_extraction_recall(
    client: httpx.AsyncClient,
    fixtures: list[dict],
    session_id: str,
) -> dict:
    extraction_fixtures = [
        f for f in fixtures
        if f.get("expected_entities", {}).get("nodes")
        and not f.get("expected_entities", {}).get("dedup_check")
    ]
    if not extraction_fixtures:
        return {"score": 1.0, "detail": "no extraction fixtures", "tested": 0}

    total_expected = 0
    total_found = 0
    details = []

    for fixture in extraction_fixtures:
        fid = fixture.get("id", "unknown")

        # Send all messages
        for msg in fixture.get("messages", []):
            await client.post(
                "/api/chat",
                json={
                    "message": msg["content"],
                    "session_id": session_id,
                    "history": [],
                },
            )

        # Fetch all current nodes
        res = await client.get("/api/nodes")
        if not res.is_success:
            details.append({"fixture": fid, "passed": False, "reason": f"HTTP {res.status_code}"})
            continue

        nodes = res.json().get("nodes", [])
        node_names_lower = {n.get("name", "").lower() for n in nodes}

        expected_nodes = fixture.get("expected_entities", {}).get("nodes", [])
        found = []
        missing = []

        for expected in expected_nodes:
            expected_name = expected.get("name", "").lower()
            # Fuzzy match: expected name is a substring of any node name
            if any(expected_name in n or n in expected_name for n in node_names_lower):
                found.append(expected.get("name"))
            else:
                missing.append(expected.get("name"))

        total_expected += len(expected_nodes)
        total_found += len(found)

        details.append({
            "fixture": fid,
            "found": found,
            "missing": missing,
            "recall": len(found) / len(expected_nodes) if expected_nodes else 1.0,
        })

    score = total_found / total_expected if total_expected > 0 else 1.0

    return {
        "score": round(score, 3),
        "total_expected": total_expected,
        "total_found": total_found,
        "tested": len(extraction_fixtures),
        "detail": details,
    }
