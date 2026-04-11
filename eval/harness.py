"""
Mind-Graph Evaluation Harness

Runs a suite of metrics against the live system to measure memory quality.
Requires the backend to be running (BACKEND_URL env var or localhost:8000).

Usage:
    python -m eval.harness
    python -m eval.harness --fixture project_intro
    python -m eval.harness --output results.json

Set ENABLE_EVAL=true in the backend .env to expose /api/eval.
"""

import asyncio
import json
import os
import sys
import argparse
from datetime import datetime

import httpx

from eval.metrics.retrieval_precision import measure_retrieval_precision
from eval.metrics.dedup_accuracy import measure_dedup_accuracy
from eval.metrics.extraction_recall import measure_extraction_recall
from eval.metrics.lifecycle_coverage import measure_lifecycle_coverage

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str = "default") -> list[dict]:
    path = os.path.join(FIXTURES_DIR, "conversations.json")
    with open(path) as f:
        all_fixtures = json.load(f)
    if name == "default":
        return all_fixtures
    return [f for f in all_fixtures if f.get("id") == name]


async def run_harness(fixture: str = "default") -> dict:
    """
    Run all evaluation metrics and return a structured JSON report.
    Each metric returns a score in [0, 1] and optional detail.
    """
    fixtures = load_fixture(fixture)
    if not fixtures:
        return {"error": f"No fixtures found for: {fixture}"}

    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=60.0) as client:
        # Verify backend is reachable
        try:
            await client.get("/health")
        except Exception as e:
            return {"error": f"Backend not reachable at {BACKEND_URL}: {e}"}

        results = {}
        session_id = f"eval_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        # Run each metric
        results["retrieval_precision"] = await measure_retrieval_precision(
            client, fixtures, session_id
        )
        results["dedup_accuracy"] = await measure_dedup_accuracy(
            client, fixtures, session_id
        )
        results["extraction_recall"] = await measure_extraction_recall(
            client, fixtures, session_id
        )
        results["lifecycle_coverage"] = await measure_lifecycle_coverage(client)

    # Compute overall score
    scores = [
        v["score"]
        for v in results.values()
        if isinstance(v, dict) and "score" in v
    ]
    overall = sum(scores) / len(scores) if scores else 0.0

    return {
        "fixture": fixture,
        "timestamp": datetime.utcnow().isoformat(),
        "overall_score": round(overall, 3),
        "metrics": results,
        "pass": overall >= 0.80,
    }


async def _main():
    parser = argparse.ArgumentParser(description="Mind-Graph evaluation harness")
    parser.add_argument("--fixture", default="default", help="Fixture name or 'default' for all")
    parser.add_argument("--output", default=None, help="Write JSON report to this file")
    args = parser.parse_args()

    report = await run_harness(fixture=args.fixture)
    output = json.dumps(report, indent=2)

    print(output)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"\nReport written to {args.output}", file=sys.stderr)

    # Exit non-zero if below threshold
    if not report.get("pass", False):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_main())
