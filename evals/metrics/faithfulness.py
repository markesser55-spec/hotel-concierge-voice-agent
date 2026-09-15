"""Faithfulness metric helpers for per-turn eval scoring."""

from __future__ import annotations

import json
from typing import Optional

from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from agent import TurnResult
from evals.metrics.judge_model import get_judge_model


def build_retrieval_context(turn_result: TurnResult) -> list[str]:
    """Collect JSON-stringified tool result payloads from live turn events."""
    context: list[str] = []
    for event in turn_result.tool_events or []:
        if event.get("event") != "result":
            continue
        result = event.get("result")
        if result is None:
            continue
        context.append(json.dumps(result, ensure_ascii=False, default=str))
    return context


def get_faithfulness_metric() -> FaithfulnessMetric:
    """FaithfulnessMetric with shared judge model; DeepEval default threshold."""
    return FaithfulnessMetric(model=get_judge_model())


def prepare_faithfulness_turn(
    user_text: str,
    turn_result: TurnResult,
) -> Optional[LLMTestCase]:
    """
    Build an LLMTestCase for FaithfulnessMetric, or None to skip the turn.

    Turns with no tool result events have no retrieval context and are skipped
    rather than faithfulness-checked with an empty context.
    """
    retrieval_context = build_retrieval_context(turn_result)
    if not retrieval_context:
        return None
    return LLMTestCase(
        input=user_text,
        actual_output=turn_result.assistant_text,
        retrieval_context=retrieval_context,
    )
