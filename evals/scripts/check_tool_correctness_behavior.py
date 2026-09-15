#!/usr/bin/env python3
"""
ONE-OFF investigation — not part of the permanent eval test suite.

Probes DeepEval ToolCorrectnessMetric behavior for ordered vs unordered configs
used by evals.metrics.tool_correctness.get_tool_correctness_metric, and
NoExtraToolCallsMetric on the same five hardcoded LLMTestCases.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from deepeval.test_case import LLMTestCase, ToolCall

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.metrics.no_extra_tool_calls import NoExtraToolCallsMetric
from evals.metrics.tool_correctness import get_tool_correctness_metric

TOOL_A = ToolCall(name="lookup_guest_reservation", input_parameters={})
TOOL_B = ToolCall(name="verify_auth_pin", input_parameters={"pin": "123456"})
TOOL_EXTRA = ToolCall(name="search_nearby_places", input_parameters={"query": "restaurants"})


def _case(
    *,
    tools_called: list[ToolCall],
    expected_tools: list[ToolCall],
) -> LLMTestCase:
    return LLMTestCase(
        input="hardcoded probe — not a real user turn",
        actual_output="hardcoded probe response",
        tools_called=tools_called,
        expected_tools=expected_tools,
    )


CASES: list[dict] = [
    {
        "id": 1,
        "label": "ordered=True, exact match correct order",
        "ordered": True,
        "expect_pass": True,
        "expect_no_extra_pass": True,
        "test_case": _case(
            tools_called=[TOOL_A, TOOL_B],
            expected_tools=[TOOL_A, TOOL_B],
        ),
    },
    {
        "id": 2,
        "label": "ordered=True, same tools reversed → expect FAIL",
        "ordered": True,
        "expect_pass": False,
        "expect_no_extra_pass": True,
        "test_case": _case(
            tools_called=[TOOL_B, TOOL_A],
            expected_tools=[TOOL_A, TOOL_B],
        ),
    },
    {
        "id": 3,
        "label": "ordered=False, same tools reversed → expect PASS",
        "ordered": False,
        "expect_pass": True,
        "expect_no_extra_pass": True,
        "test_case": _case(
            tools_called=[TOOL_B, TOOL_A],
            expected_tools=[TOOL_A, TOOL_B],
        ),
    },
    {
        "id": 4,
        "label": "ordered=False, missing expected tool → expect FAIL",
        "ordered": False,
        "expect_pass": False,
        "expect_no_extra_pass": True,
        "test_case": _case(
            tools_called=[TOOL_A],
            expected_tools=[TOOL_A, TOOL_B],
        ),
    },
    {
        "id": 5,
        "label": "ordered=False, extra unexpected tool → expect FAIL",
        "ordered": False,
        "expect_pass": False,
        "expect_no_extra_pass": False,
        "test_case": _case(
            tools_called=[TOOL_A, TOOL_B, TOOL_EXTRA],
            expected_tools=[TOOL_A, TOOL_B],
        ),
    },
]


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")

    mismatches: list[int] = []
    no_extra_mismatches: list[int] = []
    print("ToolCorrectnessMetric + NoExtraToolCallsMetric behavior probe (5 cases)")
    print("=" * 72)

    for case in CASES:
        metric = get_tool_correctness_metric(case["ordered"])
        # Deterministic name-only scoring; avoid async event-loop noise in a script.
        metric.async_mode = False
        metric.measure(case["test_case"])

        passed = bool(metric.success)
        expected = case["expect_pass"]
        ok = passed == expected
        if not ok:
            mismatches.append(case["id"])

        no_extra = NoExtraToolCallsMetric()
        no_extra.measure(case["test_case"])
        no_extra_passed = bool(no_extra.success)
        no_extra_expected = case["expect_no_extra_pass"]
        no_extra_ok = no_extra_passed == no_extra_expected
        if not no_extra_ok:
            no_extra_mismatches.append(case["id"])

        status = "MATCH" if ok else "MISMATCH — metric behavior ≠ our expectation"
        no_extra_status = (
            "MATCH" if no_extra_ok else "MISMATCH — NoExtraToolCalls ≠ expectation"
        )
        print(f"\nCase {case['id']}: {case['label']}")
        print(f"  config: ordered={case['ordered']} "
              f"(should_consider_ordering={metric.should_consider_ordering}, "
              f"should_exact_match={metric.should_exact_match})")
        print(f"  ToolCorrectness  expected: {'PASS' if expected else 'FAIL'}")
        print(f"  ToolCorrectness  actual:   {'PASS' if passed else 'FAIL'} "
              f"(score={metric.score})")
        print(f"  ToolCorrectness  reason:   {metric.reason}")
        print(f"  ToolCorrectness  → {status}")
        print(f"  NoExtraToolCalls expected: {'PASS' if no_extra_expected else 'FAIL'}")
        print(f"  NoExtraToolCalls actual:   {'PASS' if no_extra_passed else 'FAIL'} "
              f"(score={no_extra.score})")
        print(f"  NoExtraToolCalls reason:   {no_extra.reason}")
        print(f"  NoExtraToolCalls → {no_extra_status}")

    print()
    print("=" * 72)
    exit_code = 0
    if mismatches:
        print(
            f"FLAG: ToolCorrectness {len(mismatches)} case(s) did not match: "
            f"{mismatches}."
        )
        exit_code = 1
    else:
        print("ToolCorrectness: all 5 cases matched expectation.")
    if no_extra_mismatches:
        print(
            f"FLAG: NoExtraToolCalls {len(no_extra_mismatches)} case(s) did not match: "
            f"{no_extra_mismatches}."
        )
        exit_code = 1
    else:
        print("NoExtraToolCalls: all 5 cases matched expectation (1–4 PASS, 5 FAIL).")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
