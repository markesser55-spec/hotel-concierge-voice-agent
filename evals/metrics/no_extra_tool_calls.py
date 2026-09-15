"""Deterministic metric: fail when tools_called contains unexpected tool names."""

from __future__ import annotations

from typing import List, Optional

from deepeval.metrics import BaseMetric
from deepeval.metrics.indicator import metric_progress_indicator
from deepeval.metrics.utils import (
    check_llm_test_case_params,
    construct_verbose_logs,
)
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall


class NoExtraToolCallsMetric(BaseMetric):
    """
    Passes iff every tools_called name is in expected_tools (set subset).

    Catches unexpected extras that ToolCorrectnessMetric's non-exact path
    ignores. Fully deterministic — no judge model.
    """

    _required_params: List[SingleTurnParams] = [
        SingleTurnParams.TOOLS_CALLED,
        SingleTurnParams.EXPECTED_TOOLS,
    ]

    def __init__(
        self,
        threshold: float = 1.0,
        verbose_mode: bool = False,
        async_mode: bool = True,
    ) -> None:
        self.threshold = threshold
        self.verbose_mode = verbose_mode
        self.async_mode = async_mode

    def _score_test_case(self, test_case: LLMTestCase) -> float:
        called = test_case.tools_called or []
        expected = test_case.expected_tools or []
        called_names = {t.name for t in called if isinstance(t, ToolCall) and t.name}
        expected_names = {
            t.name for t in expected if isinstance(t, ToolCall) and t.name
        }
        extras = sorted(called_names - expected_names)

        if extras:
            self.score = 0.0
            listed = ", ".join(repr(n) for n in extras)
            self.reason = (
                f"Unexpected tool(s) called: {listed}. "
                f"Expected only: {sorted(expected_names)}."
            )
        else:
            self.score = 1.0
            self.reason = (
                "No unexpected tools called; "
                f"tools_called names are a subset of expected_tools "
                f"({sorted(expected_names)})."
            )

        self.success = self.is_successful()
        if self.verbose_mode:
            self.verbose_logs = construct_verbose_logs(
                self,
                steps=[
                    f"tools_called names: {sorted(called_names)}",
                    f"expected_tools names: {sorted(expected_names)}",
                    f"extras: {extras}",
                    f"Score: {self.score:.2f}",
                    f"Reason: {self.reason}",
                ],
            )
        return self.score

    def measure(
        self,
        test_case: LLMTestCase,
        _show_indicator: bool = True,
        _in_component: bool = False,
    ) -> float:
        check_llm_test_case_params(
            test_case,
            self._required_params,
            None,
            None,
            self,
            None,
            test_case.multimodal,
        )
        with metric_progress_indicator(
            self, _show_indicator=_show_indicator, _in_component=_in_component
        ):
            return self._score_test_case(test_case)

    async def a_measure(
        self,
        test_case: LLMTestCase,
        _show_indicator: bool = True,
        _in_component: bool = False,
    ) -> float:
        return self.measure(
            test_case,
            _show_indicator=_show_indicator,
            _in_component=_in_component,
        )

    @property
    def __name__(self) -> str:
        return "No Extra Tool Calls"

    def is_successful(self) -> Optional[bool]:
        if self.error is not None:
            self.success = False
        else:
            try:
                self.success = self.score >= self.threshold
            except TypeError:
                self.success = False
        return self.success
