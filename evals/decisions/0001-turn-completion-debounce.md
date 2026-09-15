# Eval harness notes

Text-only evals drive [`ConversationAgent`](agent.py) with
`tool_provider=get_eval_hotel_concierge_tools` from [`evals/tools.py`](evals/tools.py).
Production telephony never imports `evals/`.

## Turn completion and the settle debounce

### Investigation (Pipecat 0.0.108)

We looked for a built-in signal meaning “this user turn is fully done, including any
tool calls and follow-up LLM cycles,” usable from a **headless** (no STT/TTS) pipeline.

| Candidate | What it actually means | Usable for `run_turn`? |
|-----------|------------------------|-------------------------|
| `PipelineTask.on_idle_timeout` | No `BotSpeakingFrame` / `UserSpeakingFrame` for ~**300s** (abandon / cancel). | **No** — wrong timescale and frame types; headless path does not emit speaking frames. |
| `TurnTrackingObserver.on_turn_ended` | Ends after **bot stopped speaking**, then a **timer** (`turn_end_timeout_secs`, default **2.5s**) in case speech resumes (comment explicitly cites **function calls**). | **No** for headless — depends on `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` from TTS. Note: Pipecat itself uses a **debounce** for the same class of race. |
| `LLMAssistantAggregator.on_assistant_turn_stopped` | Fires when **one** `LLMFullResponseEndFrame` aggregation completes. | **Partial** — fires again after tool follow-up LLM cycles, but also fires **between** filler speech and tool execution; not “fully done” by itself. |
| `has_function_calls_in_progress` | Aggregator tracks in-flight tool calls. | **Helpful guard**, not a completion event; frame ordering races still exist between `LLMFullResponseEndFrame` and `FunctionCallInProgressFrame`. |
| LLM `on_function_calls_started` | Tools just launched. | Start signal only; no paired “all tools + follow-up LLM finished” event. |

**Conclusion:** There is no precise Pipecat event for “reasoning turn complete including tool follow-ups” on a text-only pipeline. The fixed settle window after `on_assistant_turn_stopped` (when no function calls are in progress and no LLM response is open) remains the practical approach.

### Current approach

[`ConversationAgent`](agent.py) waits for assistant-turn stop, then sleeps
`turn_settle_secs` (default **`DEFAULT_TURN_SETTLE_SECS = 0.25`**) before marking the
turn complete. Configure per agent:

```python
async with ConversationAgent(
    ani="+15551234567",
    tool_provider=get_eval_hotel_concierge_tools,
    turn_settle_secs=0.5,  # raise if flaky under load / slow tools
) as agent:
    result = await agent.run_turn("What time is checkout?")
```

### Known reliability risk (monitor)

A settle that is **too short** can return early: missing the post-tool assistant
utterance or truncating `tool_events`. A settle that is **too long** slows the suite
but is safer.

**Monitor in CI / harness logs:**

- Intermittent empty or truncated `assistant_text` on tool-using turns
- Missing `tool_events` `result` entries that appear in `messages`
- Timeouts on `run_turn` that disappear when `turn_settle_secs` is increased

If flake rates rise, bump `turn_settle_secs` before investing in a custom observer that
tracks pending tool tasks + open LLM responses end-to-end (still application-level, not
a Pipecat primitive).
