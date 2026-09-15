#!/usr/bin/env python3
"""
ONE-OFF investigation — not part of the permanent eval test suite.

Probes DeepEval FaithfulnessMetric behavior via three hardcoded LLMTestCases
with the same retrieval_context and different actual_output claims.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from deepeval.test_case import LLMTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.metrics.faithfulness import get_faithfulness_metric

RETRIEVAL_CONTEXT = [
    '{"room_type": "Ocean View Suite", "check_out_date": "2026-08-24"}',
]

CASES: list[dict] = [
    {
        "id": 1,
        "label": "grounded checkout date in retrieval context",
        "actual_output": "Your Ocean View Suite checkout is August 24th.",
        "expect_pass": True,
    },
    {
        "id": 2,
        "label": "fabricated checkout date not in retrieval context",
        "actual_output": "Your Ocean View Suite checkout is September 25th.",
        "expect_pass": False,
    },
    {
        "id": 3,
        "label": "no factual claims — closing ask only",
        "actual_output": "Is there anything else I can help with?",
        "expect_pass": True,
    },
]


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")

    metric = get_faithfulness_metric()
    metric.async_mode = False

    mismatches: list[int] = []
    print("FaithfulnessMetric behavior probe (3 cases)")
    print(f"retrieval_context={RETRIEVAL_CONTEXT}")
    print("=" * 72)

    for case in CASES:
        test_case = LLMTestCase(
            input="hardcoded probe — not a real user turn",
            actual_output=case["actual_output"],
            retrieval_context=RETRIEVAL_CONTEXT,
        )
        metric.measure(test_case)

        passed = bool(metric.success)
        expected = case["expect_pass"]
        ok = passed == expected
        if not ok:
            mismatches.append(case["id"])

        status = "MATCH" if ok else "MISMATCH — metric behavior ≠ our expectation"
        print(f"\nCase {case['id']}: {case['label']}")
        print(f"  actual_output: {case['actual_output']!r}")
        print(f"  expected: {'PASS' if expected else 'FAIL'}")
        print(f"  actual:   {'PASS' if passed else 'FAIL'} (score={metric.score})")
        print(f"  reason:   {metric.reason}")
        print(f"  → {status}")

    print()
    print("=" * 72)
    if mismatches:
        print(
            f"FLAG: {len(mismatches)} case(s) did not match expectation: {mismatches}."
        )
        return 1
    print("All 3 cases matched expectation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
