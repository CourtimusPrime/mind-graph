"""
Lifecycle Coverage metric.

Checks that temporal metadata (created_at, access_count) is present on nodes,
and that the /api/memory/health and /api/memory/prune endpoints respond correctly.

Score = fraction of lifecycle API checks that pass.
"""

import httpx


async def measure_lifecycle_coverage(client: httpx.AsyncClient) -> dict:
    checks: list[dict] = []

    # 1. Check /api/memory/health returns per-type stats
    try:
        res = await client.get("/api/memory/health")
        if res.is_success:
            data = res.json()
            has_by_type = "by_type" in data and isinstance(data["by_type"], list)
            checks.append({
                "check": "memory_health_endpoint",
                "passed": has_by_type,
                "detail": f"by_type has {len(data.get('by_type', []))} entries",
            })
        else:
            checks.append({"check": "memory_health_endpoint", "passed": False, "detail": f"HTTP {res.status_code}"})
    except Exception as e:
        checks.append({"check": "memory_health_endpoint", "passed": False, "detail": str(e)})

    # 2. Check /api/memory/prune (dry_run) returns candidates list
    try:
        res = await client.post("/api/memory/prune", json={"dry_run": True})
        if res.is_success:
            data = res.json()
            has_report = "candidates_found" in data and "dry_run" in data
            checks.append({
                "check": "prune_dry_run",
                "passed": has_report,
                "detail": f"candidates_found={data.get('candidates_found', '?')}, dry_run={data.get('dry_run', '?')}",
            })
        else:
            checks.append({"check": "prune_dry_run", "passed": False, "detail": f"HTTP {res.status_code}"})
    except Exception as e:
        checks.append({"check": "prune_dry_run", "passed": False, "detail": str(e)})

    # 3. Check /api/nodes includes created_at and access_count fields
    try:
        res = await client.get("/api/nodes")
        if res.is_success:
            nodes = res.json().get("nodes", [])
            if nodes:
                sample = nodes[0]
                has_temporal = "access_count" in sample
                # created_at may be null for old nodes but the field should be present
                has_created_at = "created_at" in sample
                checks.append({
                    "check": "nodes_have_temporal_fields",
                    "passed": has_temporal and has_created_at,
                    "detail": f"access_count present={has_temporal}, created_at present={has_created_at}",
                })
            else:
                checks.append({
                    "check": "nodes_have_temporal_fields",
                    "passed": True,
                    "detail": "no nodes to check (empty graph)",
                })
        else:
            checks.append({"check": "nodes_have_temporal_fields", "passed": False, "detail": f"HTTP {res.status_code}"})
    except Exception as e:
        checks.append({"check": "nodes_have_temporal_fields", "passed": False, "detail": str(e)})

    passed = sum(1 for c in checks if c["passed"])
    score = passed / len(checks) if checks else 1.0

    return {
        "score": round(score, 3),
        "passed": passed,
        "tested": len(checks),
        "detail": checks,
    }
