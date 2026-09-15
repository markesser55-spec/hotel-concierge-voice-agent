"""Replay golden conversations through ConversationAgent; score tool correctness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent import ConversationAgent
from evals.metrics.faithfulness import get_faithfulness_metric, prepare_faithfulness_turn
from evals.metrics.tool_correctness import prepare_tool_correctness_turn
from evals.tools import get_eval_hotel_concierge_tools

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "datasets" / "golden_v1.jsonl"
)


def load_golden_conversations() -> list[dict[str, Any]]:
    """Load golden_v1.jsonl once (called at collection / import time)."""
    conversations: list[dict[str, Any]] = []
    text = GOLDEN_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        conversations.append(json.loads(line))
    return conversations


GOLDEN_CONVERSATIONS = load_golden_conversations()


@pytest.mark.parametrize(
    "conversation",
    GOLDEN_CONVERSATIONS,
    ids=lambda c: c["conversation_id"],
)
async def test_conversation(conversation: dict[str, Any]) -> None:
    """Replay one golden conversation turn-by-turn; assert tool metrics at the end."""
    failures: list[str] = []
    tags = list(conversation.get("tags") or [])
    ani = str(conversation.get("ani") or "+15550000000")

    async with ConversationAgent(
        ani=ani,
        tool_provider=get_eval_hotel_concierge_tools,
    ) as agent:
        for turn in conversation.get("turns") or []:
            turn_number = turn.get("turn_number")
            user_text = str(turn.get("user_text") or "")
            turn_result = await agent.run_turn(user_text)

            test_case, tool_metric, no_extra_metric = prepare_tool_correctness_turn(
                turn,
                turn_result,
                tags,
            )
            await tool_metric.a_measure(test_case)
            await no_extra_metric.a_measure(test_case)

            if not tool_metric.success:
                failures.append(
                    f"turn {turn_number} ToolCorrectnessMetric failed "
                    f"(score={tool_metric.score}): {tool_metric.reason}"
                )
            if not no_extra_metric.success:
                failures.append(
                    f"turn {turn_number} NoExtraToolCallsMetric failed "
                    f"(score={no_extra_metric.score}): {no_extra_metric.reason}"
                )

            faithfulness_case = prepare_faithfulness_turn(user_text, turn_result)
            if faithfulness_case is not None:
                faithfulness_metric = get_faithfulness_metric()
                await faithfulness_metric.a_measure(faithfulness_case)
                if not faithfulness_metric.success:
                    failures.append(
                        f"turn {turn_number} FaithfulnessMetric "
                        f"(score={faithfulness_metric.score}): {faithfulness_metric.reason}"
                    )

    assert not failures, "\n".join(failures)
