#!/usr/bin/env python3
"""
ONE-OFF connectivity check — not part of the permanent eval test suite.

Verifies GEMINI_API_KEY + get_judge_model() work with DeepEval's
AnswerRelevancyMetric against a trivial hardcoded LLMTestCase.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from deepeval.metrics import AnswerRelevancyMetric
from deepeval.test_case import LLMTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.metrics.judge_model import get_judge_model


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")

    test_case = LLMTestCase(
        input="What is the capital of France?",
        actual_output="The capital of France is Paris.",
    )
    metric = AnswerRelevancyMetric(model=get_judge_model())
    metric.measure(test_case)

    print(f"score: {metric.score}")
    print(f"reason: {metric.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
