"""Tool-correctness metric helpers for per-turn eval scoring."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from deepeval.metrics import ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase, ToolCall

from agent import TurnResult
from evals.metrics.judge_model import get_judge_model
from evals.metrics.no_extra_tool_calls import NoExtraToolCallsMetric

# Parallel tool use: order must not be required.
_PARALLEL_TAG = "scenario-5-parallel-tool-calling"
# Sequential auth / mutation flows: order matters when multiple tools fire.
_SEQUENTIAL_TAG_EXACT = "scenario-3-reservation-lookup"
_SEQUENTIAL_TAG_PREFIX = "scenario-4-modification"


def should_consider_tool_order(conversation_tags: Sequence[str]) -> bool:
    """
    Choose ordered vs unordered ToolCorrectnessMetric for this conversation.

    - scenario-5-parallel-tool-calling → unordered (ordered=False)
    - scenario-3-reservation-lookup / scenario-4-modification* → ordered
    - default → ordered (harmless for single-tool turns)
    """
    tags = list(conversation_tags or [])
    if _PARALLEL_TAG in tags:
        return False
    for tag in tags:
        if tag == _SEQUENTIAL_TAG_EXACT or tag.startswith(_SEQUENTIAL_TAG_PREFIX):
            return True
    return True


def get_tool_correctness_metric(ordered: bool) -> ToolCorrectnessMetric:
    """
    Name-only ToolCorrectnessMetric (no evaluation_params — arg matching deferred).

    ordered=True  → should_consider_ordering=True, threshold=1.0
    ordered=False → should_consider_ordering=False, threshold=1.0 (set match, order ignored)
    """
    if ordered:
        return ToolCorrectnessMetric(
            model=get_judge_model(),
            should_consider_ordering=True,
            threshold=1.0,
            evaluation_params=[],
        )
    return ToolCorrectnessMetric(
        model=get_judge_model(),
        should_consider_ordering=False,
        threshold=1.0,
        evaluation_params=[],
    )


def _tool_calls_from_expected(
    expected_tool_calls: Sequence[Mapping[str, Any]] | None,
) -> list[ToolCall]:
    tools: list[ToolCall] = []
    for item in expected_tool_calls or []:
        name = item.get("name")
        if not name:
            continue
        args = item.get("args")
        tools.append(
            ToolCall(
                name=str(name),
                input_parameters=dict(args) if isinstance(args, Mapping) else {},
            )
        )
    return tools


def _tool_calls_from_turn_result(turn_result: TurnResult) -> list[ToolCall]:
    """Build ToolCall list from live started tool events (call order preserved)."""
    tools: list[ToolCall] = []
    for event in turn_result.tool_events or []:
        if event.get("event") != "started":
            continue
        name = event.get("name")
        if not name:
            continue
        args = event.get("arguments")
        tools.append(
            ToolCall(
                name=str(name),
                input_parameters=dict(args) if isinstance(args, Mapping) else {},
            )
        )
    return tools


def build_tool_correctness_test_case(
    *,
    user_text: str,
    expected_tool_calls: Sequence[Mapping[str, Any]] | None,
    turn_result: TurnResult,
) -> LLMTestCase:
    """LLMTestCase for ToolCorrectnessMetric from a golden turn + live TurnResult."""
    return LLMTestCase(
        input=user_text,
        actual_output=turn_result.assistant_text,
        tools_called=_tool_calls_from_turn_result(turn_result),
        expected_tools=_tool_calls_from_expected(expected_tool_calls),
    )


def prepare_tool_correctness_turn(
    golden_turn: Mapping[str, Any],
    turn_result: TurnResult,
    conversation_tags: Sequence[str],
) -> tuple[LLMTestCase, ToolCorrectnessMetric, NoExtraToolCallsMetric]:
    """
    Per-turn helper: build LLMTestCase + ToolCorrectness + NoExtraToolCalls metrics.

    Ordering for ToolCorrectness is chosen from conversation tags
    (parallel vs sequential scenarios). NoExtraToolCalls is always attached —
    it catches unexpected extras that ToolCorrectness's non-exact path ignores.
    """
    ordered = should_consider_tool_order(conversation_tags)
    test_case = build_tool_correctness_test_case(
        user_text=str(golden_turn.get("user_text") or ""),
        expected_tool_calls=golden_turn.get("expected_tool_calls"),
        turn_result=turn_result,
    )
    return (
        test_case,
        get_tool_correctness_metric(ordered),
        NoExtraToolCallsMetric(),
    )
