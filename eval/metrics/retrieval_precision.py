"""
Retrieval Precision metric.

For fixtures with a retrieval_check, sends setup messages to populate the graph,
then queries the search endpoint and checks whether expected terms appear in results.

Score = fraction of retrieval_check fixtures where all expected_in_context terms
        are found in the top search results.
"""

import httpx


async def measure_retrieval_precision(
    client: httpx.AsyncClient,
    fixtures: list[dict],
    session_id: str,
) -> dict:
    retrieval_fixtures = [f for f in fixtures if "retrieval_check" in f or "query" in f]
    if not retrieval_fixtures:
        return {"score": 1.0, "detail": "no retrieval fixtures", "tested": 0}

    passed = 0
    details = []

    for fixture in retrieval_fixtures:
        fid = fixture.get("id", "unknown")

        # Seed the graph with setup messages
        for msg in fixture.get("setup_messages", []):
            await client.post(
                "/api/chat",
                json={
                    "message": msg["content"],
                    "session_id": session_id,
                    "history": [],
                },
            )

        query = fixture.get("query", "")
        expected = fixture.get("expected_in_context", [])

        if not query or not expected:
            continue

        # Search for the query
        res = await client.get("/api/search", params={"q": query, "limit": 10})
        if not res.is_success:
            details.append({"fixture": fid, "passed": False, "reason": f"search HTTP {res.status_code}"})
            continue

        results = res.json().get("results", [])
        result_text = " ".join(
            f"{r.get('name', '')} {r.get('content', '')}"
            for r in results
        ).lower()

        found = [term for term in expected if term.lower() in result_text]
        fixture_pass = len(found) == len(expected)

        if fixture_pass:
            passed += 1

        details.append({
            "fixture": fid,
            "passed": fixture_pass,
            "expected": expected,
            "found": found,
            "missing": [t for t in expected if t not in found],
        })

    score = passed / len(retrieval_fixtures) if retrieval_fixtures else 1.0

    return {
        "score": round(score, 3),
        "passed": passed,
        "tested": len(retrieval_fixtures),
        "detail": details,
    }
