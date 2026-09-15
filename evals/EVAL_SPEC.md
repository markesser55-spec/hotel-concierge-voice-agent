# EVAL_SPEC

Specification for the automated eval framework that grades the hotel concierge
voice agent’s conversational reasoning.

> Canonical location: [`evals/EVAL_SPEC.md`](EVAL_SPEC.md).  
> Root [`EVAL_SPEC.md`](../EVAL_SPEC.md) is a short pointer to this file.

## Scope

This framework evaluates **conversational reasoning** and **tool-calling correctness**
by driving [`ConversationAgent.run_turn`](../agent.py) with text utterances. It does not
exercise the Twilio Media Streams path, STT, or TTS.

## Architecture

Evals construct a `ConversationAgent` and call `run_turn` turn-by-turn. Under the hood,
`run_turn` starts a **headless Pipecat pipeline** (`user aggregator → Gemini LLM →
assistant aggregator`) with no transport, STT, or TTS—the same `GoogleLLMService` tool
loop used on live calls, without audio.

Tools are supplied via an injectable **`tool_provider: Callable[[str], list]`**.
Production defaults to [`tools.get_hotel_concierge_tools`](../tools.py) (real MCP + SMS).
The eval harness passes [`evals.tools.get_eval_hotel_concierge_tools`](tools.py)
instead. The mock tool provider starts unauthenticated. `verify_auth_pin` always
succeeds regardless of PIN value (no real validation, still fully mocked — no MCP or
SMS), but must be called before `lookup_guest_reservation` will return data. The
unauthenticated lookup status is **`auth_required`** (matching `SYSTEM_PROMPT` Rule 4).
This gate exists to preserve tool-call ordering fidelity with real historical call data,
not to test authentication security — revised from the original v1 design (which skipped
auth entirely) after golden dataset replay showed real conversations treat the PIN flow
as causally required, not separable filler. Mock tools use local fixtures and **must not**
import MCP, Twilio Verify, or production `tools.py`—so eval-mode calls cannot reach
those systems by construction. `agent.py` / `pipeline.py` / `server.py` never import
`evals/`.

Turn-completion detection (settle debounce after assistant-turn stop) is documented in
[`evals/decisions/0001-turn-completion-debounce.md`](decisions/0001-turn-completion-debounce.md);
do not duplicate that detail here.

**Tracing:** Live Langfuse/OTel export is configured only in `server.py` and enabled on
the audio `PipelineTask` in `pipeline.py`. Headless `run_turn` does not enable tracing
and does not load the OTLP exporter.

## Data Source

Golden dataset entries are sourced from real historical Langfuse traces, not synthetic
data. Real traces are candidates, not automatically trusted — during curation, any
turn identified as reflecting a known application defect (not merely an ambiguous
transcript) is retained for conversational context but tagged with a `needs_review`
reason explaining the issue, and is not treated as a correctness target until the
underlying behavior is fixed and the entry is re-validated.

Extractor: [`evals/scripts/extract_dataset_from_langfuse.py`](scripts/extract_dataset_from_langfuse.py)
(uses `client.api.observations.get_many` — Langfuse v2 observations API). Merges into
`from_langfuse.jsonl` by `conversation_id` (replace-or-append). Rejects for the run are
written to `_extraction_rejects.jsonl` (truncated each run).

```bash
python evals/scripts/extract_dataset_from_langfuse.py \
  --trace-id <trace_id> \
  [--out evals/datasets/from_langfuse.jsonl] \
  [--rejects evals/datasets/_extraction_rejects.jsonl]
```

Assembler: [`evals/scripts/assemble_golden_dataset.py`](scripts/assemble_golden_dataset.py)
reads `from_langfuse.jsonl` + [`datasets/curation.json`](datasets/curation.json) and
writes [`datasets/golden_v1.jsonl`](datasets/golden_v1.jsonl).

Output shape (one JSON object per conversation / `source_trace_id`):

```json
{
  "conversation_id": "<uuid5 of source_trace_id>",
  "source_trace_id": "<langfuse_trace_id>",
  "session_id": "<langfuse_session_id>",
  "ani": "+1555xxxxxxx",
  "tags": ["scenario-…"],
  "turns": [
    {
      "turn_number": 1,
      "user_text": "...",
      "expected_assistant_text": "...",
      "expected_tool_calls": [{ "name": "...", "args": {} }]
    }
  ]
}
```

`conversation_id` and `ani` are derived deterministically from `source_trace_id`
(UUID5 / digit hash), so re-running extraction on unchanged traces is
byte-identical. `ani` is always a synthetic placeholder (never the source ANI).
`session_id` is copied from observation `sessionId` (Twilio `CA…` for voice
sessions) for traceability alongside `source_trace_id`.
Turns that fail extraction rules
(multi-distinct STT, missing stt/llm, missing `turn.number`, unparseable LLM
I/O, etc.) are appended to `_extraction_rejects.jsonl` and omitted from the
clean dataset; a mid-trace reject stops further turns and records how many
subsequent turns were dropped.

### Current golden set (v1)

Six curated conversations in `golden_v1.jsonl` (via `curation.json`), covering:

| Scenario tags | Coverage |
|---------------|----------|
| `scenario-1-places-lookup` | Nearby places tool use |
| `scenario-2-policy-lookup` | Hotel policies (including spa) |
| `scenario-3-reservation-lookup` | Auth + reservation read |
| `scenario-5-parallel-tool-calling` | Parallel places + policies (`flagship`) |
| `scenario-6-escalation` | `escalate_to_human` |

Scenario-4 modification goldens were removed from the curated set after headless
replay showed intermittent post-tool LLM hangs on that flow; raw extractions may still
exist in `from_langfuse.jsonl`.

## Metrics

Per turn, [`evals/tests/test_golden_conversations.py`](tests/test_golden_conversations.py)
replays the golden user text, then collects failures and asserts once per conversation.

| Metric | Module | Behavior |
|--------|--------|----------|
| **ToolCorrectness** | [`metrics/tool_correctness.py`](metrics/tool_correctness.py) | DeepEval name-only tool match; `threshold=1.0`. Ordering on for reservation / modification tags; off for `scenario-5-parallel-tool-calling`. |
| **NoExtraToolCalls** | [`metrics/no_extra_tool_calls.py`](metrics/no_extra_tool_calls.py) | Fails if live tools are not a subset of expected (catches extras ToolCorrectness can miss). |
| **Faithfulness** | [`metrics/faithfulness.py`](metrics/faithfulness.py) | DeepEval `FaithfulnessMetric` with shared Gemini judge (`gemini-2.5-flash`). Retrieval context = JSON-stringified `tool_events` results. **Skipped** (no failure) when a turn has no tool results. Default DeepEval threshold (0.5). |

Judge model: [`metrics/judge_model.py`](metrics/judge_model.py) — `GEMINI_API_KEY`, temperature left at DeepEval default (0.0).

## Execution Environment

Evals run against the **imported `ConversationAgent` in-process**, not against the
deployed Cloud Run voice service. That is a deliberate **v1** scoping decision: faster
iteration and no telephony dependency.

```bash
pip install -r requirements.txt -r requirements-eval.txt
pytest evals/tests/test_golden_conversations.py -v
```

Requires **`GEMINI_API_KEY`** (agent + judge). `pytest.ini` sets `asyncio_mode = auto` and
`asyncio_default_fixture_loop_scope = function`.

**CI:** [`.github/workflows/eval-suite.yml`](../.github/workflows/eval-suite.yml) runs the
same pytest on every pull request (`timeout-minutes: 15`, concurrency cancel-in-progress
per PR). Injects `secrets.GEMINI_API_KEY` only on the pytest step.

**Future enhancement:** exercise the deployed HTTP/WebSocket endpoint (documented here
so it is not mistaken for an accidental omission).

## Out of Scope

The following are **explicitly not** evaluated by this framework—a deliberate boundary,
not an oversight:

- STT accuracy
- TTS quality
- Latency
- Interruption / barge-in handling
- Auth security (eval PIN always succeeds; gate is for ordering fidelity only)

VAD-driven duplicate turn boundaries (the live system finalizing on a partial utterance
before the complete one registers) are an expected characteristic of the production
pipeline, not a defect. The extraction pipeline collapses these to the turn reflecting
the complete utterance; dropped duplicates are logged for traceability, not treated as
data-quality failures.
