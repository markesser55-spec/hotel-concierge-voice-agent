"""
Conversation reasoning core
===========================
Owns Gemini + LLMContext + tools. Usable from the live Pipecat audio pipeline
or from text-only evals via run_turn() — no Twilio Media Streams, STT, or TTS.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    EndFrame,
    FunctionCallResultFrame,
    LLMMessagesAppendFrame,
    StartFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)

import tools
from prompts import SYSTEM_PROMPT
from services.llm import get_llm_service

ToolProvider = Callable[[str], list]

# Default wait after on_assistant_turn_stopped before treating the turn as done.
# Covers tool follow-up LLM cycles; see evals/decisions/0001-turn-completion-debounce.md.
DEFAULT_TURN_SETTLE_SECS = 0.25


@dataclass
class TurnResult:
    """Result of one user → assistant reasoning turn."""

    assistant_text: str
    messages: list
    tool_events: list = field(default_factory=list)


class ConversationAgent:
    """Session-scoped conversation brain: system prompt, Gemini, tools, history.

    Production passes the default tool_provider (real MCP/SMS tools). Eval harnesses
    inject a mock provider. This class only calls tool_provider(ani) and registers
    whatever callables are returned.
    """

    def __init__(
        self,
        ani: str,
        tool_provider: ToolProvider = tools.get_hotel_concierge_tools,
        *,
        turn_settle_secs: float = DEFAULT_TURN_SETTLE_SECS,
    ):
        self._ani = ani
        self._turn_settle_secs = turn_settle_secs
        self._llm = get_llm_service()

        dynamic_tools = tool_provider(ani)
        tools_schema = ToolsSchema(standard_tools=dynamic_tools)

        self._context = LLMContext(
            messages=[{"role": "system", "content": SYSTEM_PROMPT}],
            tools=tools_schema,
        )

        for tool_func in dynamic_tools:
            self._llm.register_direct_function(tool_func)

        # Headless text pipeline (created lazily on first run_turn)
        self._headless_task: Optional[PipelineTask] = None
        self._headless_runner_task: Optional[asyncio.Task] = None
        self._assistant_agg = None
        self._pipeline_ready = asyncio.Event()
        self._turn_lock = asyncio.Lock()

        # Per-turn collectors (reset at the start of each run_turn)
        self._assistant_chunks: List[str] = []
        self._tool_events: List[dict[str, Any]] = []
        self._turn_complete: Optional[asyncio.Event] = None
        self._llm_response_open = False
        self._settle_task: Optional[asyncio.Task] = None
        self._handlers_attached = False

    @property
    def llm(self):
        return self._llm

    @property
    def context(self) -> LLMContext:
        return self._context

    @property
    def ani(self) -> str:
        return self._ani

    @property
    def turn_settle_secs(self) -> float:
        return self._turn_settle_secs

    async def run_turn(self, user_text: str, *, timeout_secs: float = 60.0) -> TurnResult:
        """Text-in / text-out turn using the same GoogleLLMService + tool loop as live calls.

        No Twilio Media Streams, STT, TTS, or transport. Not for concurrent use with
        build_pipeline on the same agent instance (each path links the LLM into a pipeline).
        """
        await self._ensure_headless_pipeline()

        async with self._turn_lock:
            self._assistant_chunks = []
            self._tool_events = []
            self._turn_complete = asyncio.Event()
            self._llm_response_open = False
            if self._settle_task and not self._settle_task.done():
                self._settle_task.cancel()
            self._settle_task = None
            messages_before = len(self._context.get_messages())

            await self._headless_task.queue_frames(
                [
                    LLMMessagesAppendFrame(
                        [{"role": "user", "content": user_text}],
                        run_llm=True,
                    )
                ]
            )

            try:
                logger.info(
                    f"[ConversationAgent][diag] run_turn waiting for turn_complete "
                    f"(timeout={timeout_secs}s ani={self._ani})"
                )
                await asyncio.wait_for(self._turn_complete.wait(), timeout=timeout_secs)
                logger.info(
                    f"[ConversationAgent][diag] run_turn wait_for completed "
                    f"(ani={self._ani})"
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"[ConversationAgent] run_turn timed out after {timeout_secs}s "
                    f"(ani={self._ani})"
                )
                raise

            # Tool results appended to context during this turn (fallback if observer missed)
            seen_results = {
                (e.get("name"), str(e.get("result")))
                for e in self._tool_events
                if e.get("event") == "result"
            }
            for msg in self._context.get_messages()[messages_before:]:
                if not isinstance(msg, dict):
                    continue
                role = str(msg.get("role", "")).lower()
                if role in ("tool", "function"):
                    name = msg.get("name", "unknown")
                    content = msg.get("content")
                    key = (name, str(content))
                    if key not in seen_results:
                        self._tool_events.append(
                            {"event": "result", "name": name, "result": content}
                        )

            assistant_text = " ".join(
                c.strip() for c in self._assistant_chunks if c and c.strip()
            )
            return TurnResult(
                assistant_text=assistant_text,
                messages=list(self._context.get_messages()),
                tool_events=list(self._tool_events),
            )

    async def aclose(self) -> None:
        """Shut down the headless eval pipeline if it was started."""
        if self._settle_task and not self._settle_task.done():
            self._settle_task.cancel()
            self._settle_task = None
        if self._headless_task is None:
            return
        try:
            await self._headless_task.queue_frames([EndFrame()])
            if self._headless_runner_task is not None:
                await asyncio.wait_for(self._headless_runner_task, timeout=10.0)
        except Exception as e:
            logger.warning(f"[ConversationAgent] aclose: {e}")
        finally:
            self._headless_task = None
            self._headless_runner_task = None
            self._assistant_agg = None
            self._handlers_attached = False
            self._pipeline_ready.clear()

    async def __aenter__(self) -> "ConversationAgent":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _ensure_headless_pipeline(self) -> None:
        if self._headless_task is not None:
            await self._pipeline_ready.wait()
            return

        context_aggregator = LLMContextAggregatorPair(
            self._context,
            user_params=LLMUserAggregatorParams(user_idle_timeout=0),
        )
        self._assistant_agg = context_aggregator.assistant()
        self._attach_turn_handlers()

        class _ReadyObserver(BaseObserver):
            def __init__(self, ready: asyncio.Event):
                super().__init__()
                self._ready = ready

            async def on_push_frame(self, data: FramePushed):
                if isinstance(data.frame, StartFrame):
                    self._ready.set()

        class _ToolObserver(BaseObserver):
            def __init__(self, agent: "ConversationAgent"):
                super().__init__()
                self._agent = agent

            async def on_push_frame(self, data: FramePushed):
                frame = data.frame
                if isinstance(frame, FunctionCallResultFrame):
                    self._agent._tool_events.append(
                        {
                            "event": "result",
                            "name": frame.function_name,
                            "result": frame.result,
                        }
                    )

        ready_observer = _ReadyObserver(self._pipeline_ready)
        tool_observer = _ToolObserver(self)

        pipeline = Pipeline(
            [
                context_aggregator.user(),
                self._llm,
                self._assistant_agg,
            ]
        )
        self._headless_task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=False,
                enable_metrics=False,
                enable_usage_metrics=False,
            ),
            observers=[ready_observer, tool_observer],
        )

        async def _run_runner():
            runner = PipelineRunner(handle_sigint=False)
            await runner.run(self._headless_task)

        self._headless_runner_task = asyncio.create_task(_run_runner())
        await self._pipeline_ready.wait()

    def _attach_turn_handlers(self) -> None:
        """Attach once-per-pipeline handlers that read current-turn collector state."""
        if self._handlers_attached:
            return

        @self._assistant_agg.event_handler("on_assistant_turn_started")
        async def on_assistant_turn_started(_aggregator):
            logger.info("[ConversationAgent][diag] on_assistant_turn_started")
            self._llm_response_open = True
            if self._settle_task and not self._settle_task.done():
                self._settle_task.cancel()

        @self._assistant_agg.event_handler("on_assistant_turn_stopped")
        async def on_assistant_turn_stopped(aggregator, message):
            logger.info(
                "[ConversationAgent][diag] on_assistant_turn_stopped "
                f"content={message.content!r} "
                f"has_function_calls_in_progress={aggregator.has_function_calls_in_progress}"
            )
            self._llm_response_open = False
            if message.content:
                self._assistant_chunks.append(message.content)
            # Tools or a follow-up LLM cycle may still be pending — debounce.
            if aggregator.has_function_calls_in_progress:
                return
            self._schedule_turn_settle(aggregator)

        @self._llm.event_handler("on_function_calls_started")
        async def on_function_calls_started(_service, function_calls):
            logger.info(
                "[ConversationAgent][diag] on_function_calls_started "
                f"names={[getattr(fc, 'function_name', None) for fc in function_calls]}"
            )
            for fc in function_calls:
                self._tool_events.append(
                    {
                        "event": "started",
                        "name": fc.function_name,
                        "arguments": getattr(fc, "arguments", None),
                    }
                )

        self._handlers_attached = True

    def _schedule_turn_settle(self, aggregator) -> None:
        """Complete the turn only after tools and any follow-up LLM cycle settle."""
        if self._settle_task and not self._settle_task.done():
            self._settle_task.cancel()

        settle_secs = self._turn_settle_secs

        async def _settle():
            try:
                logger.info(
                    f"[ConversationAgent][diag] _settle start sleep={settle_secs}s"
                )
                await asyncio.sleep(settle_secs)
                if self._turn_complete is None or self._turn_complete.is_set():
                    logger.info(
                        "[ConversationAgent][diag] _settle early-return: "
                        f"turn_complete is None={self._turn_complete is None} "
                        f"already_set="
                        f"{self._turn_complete.is_set() if self._turn_complete else None}"
                    )
                    return
                if self._llm_response_open or aggregator.has_function_calls_in_progress:
                    logger.info(
                        "[ConversationAgent][diag] _settle early-return: "
                        f"llm_response_open={self._llm_response_open} "
                        f"has_function_calls_in_progress="
                        f"{aggregator.has_function_calls_in_progress}"
                    )
                    return
                logger.info("[ConversationAgent][diag] _settle setting turn_complete")
                self._turn_complete.set()
            except asyncio.CancelledError:
                logger.info("[ConversationAgent][diag] _settle cancelled")
                return

        self._settle_task = asyncio.create_task(_settle())
