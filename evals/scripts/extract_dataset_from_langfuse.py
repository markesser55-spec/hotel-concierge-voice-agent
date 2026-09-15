#!/usr/bin/env python3
"""
Extract conversation dataset docs from Langfuse v2 observations.

Uses client.api.observations.get_many (GET /api/public/v2/observations).
Does not call the deprecated api.trace.get path.

See evals/EVAL_SPEC.md and the locked extraction decisions in the plan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langfuse import Langfuse

from _langfuse_utils import fetch_all_observations_v2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "evals" / "datasets" / "from_langfuse.jsonl"
DEFAULT_REJECTS = REPO_ROOT / "evals" / "datasets" / "_extraction_rejects.jsonl"

# Stable namespaces so re-extracting the same trace_id yields identical ids/ANI.
_CONVERSATION_ID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_URL
_ANI_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_OID



class ParseError(Exception):
    """LLM wire-format parse failure."""


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class FunctionCallPart:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class FunctionResponsePart:
    id: str
    name: str
    response: dict[str, Any]


Part = TextPart | FunctionCallPart | FunctionResponsePart


@dataclass(frozen=True)
class Message:
    role: str
    parts: list[Part]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _attrs(obs: dict[str, Any]) -> dict[str, Any]:
    meta = obs.get("metadata") or {}
    if not isinstance(meta, dict):
        return {}
    attrs = meta.get("attributes") or {}
    return attrs if isinstance(attrs, dict) else {}


def _normalize_transcript(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _resolve_conversation_parent(
    observations: list[dict[str, Any]],
    turn_spans: list[dict[str, Any]],
    trace_id: str,
) -> dict[str, Any] | None:
    """Prefer SPAN name==conversation; else unique parent of turns; else t-{id}."""
    by_id = {o["id"]: o for o in observations}
    named = [
        o
        for o in observations
        if o.get("type") == "SPAN" and o.get("name") == "conversation"
    ]
    if named:
        return named[0]

    turn_ids = {o["id"] for o in turn_spans}
    parents = {
        o.get("parentObservationId")
        for o in turn_spans
        if o.get("parentObservationId") and o.get("parentObservationId") not in turn_ids
    }
    if len(parents) == 1:
        pid = next(iter(parents))
        if pid in by_id:
            return by_id[pid]

    synthetic = f"t-{trace_id}"
    if synthetic in by_id:
        return by_id[synthetic]

    roots = [o for o in observations if o.get("isRootObservation")]
    return roots[0] if roots else None


def find_orphan_llm_in_turn_window(
    observations: list[dict[str, Any]],
    *,
    conversation_parent_id: str | None,
    turn: dict[str, Any],
    turn_ids: set[str],
) -> dict[str, Any] | None:
    """
    Detection-only: first llm parented to conversation/root whose startTime
    falls within this turn's [startTime, endTime]. Does not reattach.
    """
    if not conversation_parent_id:
        return None
    t_start = _parse_time(turn.get("startTime"))
    t_end = _parse_time(turn.get("endTime"))
    if t_start is None:
        return None

    candidates: list[dict[str, Any]] = []
    for o in observations:
        if o.get("parentObservationId") != conversation_parent_id:
            continue
        if o.get("id") in turn_ids:
            continue
        if (o.get("name") or "").lower() != "llm":
            continue
        st = _parse_time(o.get("startTime"))
        if st is None or st < t_start:
            continue
        if t_end is not None and st > t_end:
            continue
        candidates.append(o)

    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("startTime") or "")
    return candidates[0]


def conversation_id_for_trace(source_trace_id: str) -> str:
    """Deterministic conversation_id (UUID5) keyed on source_trace_id."""
    return str(uuid.uuid5(_CONVERSATION_ID_NS, f"langfuse-trace:{source_trace_id}"))


def synthetic_ani_for_trace(source_trace_id: str) -> str:
    """Deterministic placeholder ANI; never export the Langfuse/source value."""
    digest = uuid.uuid5(_ANI_NS, f"langfuse-ani:{source_trace_id}")
    # 7 digits in 0000000..9999999 from the UUID integer.
    return f"+1555{digest.int % 10_000_000:07d}"


def session_id_from_observations(observations: list[dict[str, Any]]) -> str | None:
    """First non-empty top-level sessionId on any observation for this trace."""
    for o in observations:
        sid = o.get("sessionId")
        if sid:
            return str(sid)
    return None


def parse_part(part: dict[str, Any], *, role: str) -> Part:
    if "text" in part:
        return TextPart(str(part["text"]))
    if "function_call" in part:
        if role != "model":
            raise ParseError("function_call part on non-model message")
        fc = part["function_call"]
        if not isinstance(fc, dict):
            raise ParseError("function_call must be an object")
        args = fc.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ParseError("function_call.args must be an object")
        return FunctionCallPart(
            id=str(fc.get("id") or ""),
            name=str(fc["name"]),
            args=args,
        )
    if "function_response" in part:
        if role != "user":
            raise ParseError("function_response part on non-user message")
        fr = part["function_response"]
        if not isinstance(fr, dict):
            raise ParseError("function_response must be an object")
        response = fr.get("response")
        if response is None:
            response = {}
        if not isinstance(response, dict):
            raise ParseError("function_response.response must be an object")
        return FunctionResponsePart(
            id=str(fr.get("id") or ""),
            name=str(fr["name"]),
            response=response,
        )
    raise ParseError(f"unknown part keys: {sorted(part.keys())}")


def parse_message(raw: Any) -> Message:
    if not isinstance(raw, dict):
        raise ParseError("message must be an object")
    role = raw.get("role")
    if role not in ("user", "model"):
        raise ParseError(f"invalid role: {role!r}")
    parts_raw = raw.get("parts") or []
    if not isinstance(parts_raw, list):
        raise ParseError("parts must be a list")
    parts = [parse_part(p, role=role) for p in parts_raw if isinstance(p, dict)]
    if len(parts) != len(parts_raw):
        raise ParseError("parts list contained non-objects")
    return Message(role=role, parts=parts)


def parse_messages(raw: Any) -> list[Message]:
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list):
        raise ParseError("llm.input must be a JSON list of messages")
    return [parse_message(m) for m in data]


def has_text_part(message: Message) -> bool:
    return any(isinstance(p, TextPart) for p in message.parts)


def normalize_output_to_parts(output: Any) -> list[Part]:
    """Normalize llm.output to Part list. Plain strings become TextPart."""
    if output is None:
        return []
    if isinstance(output, str):
        s = output.strip()
        if not s:
            return []
        if s.startswith("{") or s.startswith("["):
            try:
                return normalize_output_to_parts(json.loads(s))
            except json.JSONDecodeError:
                return [TextPart(s)]
        return [TextPart(s)]
    if isinstance(output, list):
        parts: list[Part] = []
        for item in output:
            parts.extend(normalize_output_to_parts(item))
        return parts
    if isinstance(output, dict):
        if "parts" in output and "role" in output:
            return list(parse_message(output).parts)
        if "parts" in output:
            parts_raw = output.get("parts") or []
            if not isinstance(parts_raw, list):
                raise ParseError("output.parts must be a list")
            # role unknown for bare parts wrapper — infer from part keys
            out: list[Part] = []
            for p in parts_raw:
                if not isinstance(p, dict):
                    raise ParseError("output part must be an object")
                role = "model" if "function_call" in p else "user" if "function_response" in p else "model"
                out.append(parse_part(p, role=role))
            return out
        if "text" in output or "function_call" in output or "function_response" in output:
            role = (
                "model"
                if "function_call" in output or "text" in output
                else "user"
            )
            return [parse_part(output, role=role)]
        raise ParseError(f"unrecognized output object keys: {sorted(output.keys())}")
    raise ParseError(f"unsupported output type: {type(output).__name__}")


def assistant_text_from_llm_output(output: Any) -> str:
    parts = normalize_output_to_parts(output)
    chunks = [
        p.text.strip()
        for p in parts
        if isinstance(p, TextPart) and p.text.strip()
    ]
    return " ".join(chunks)


def tool_calls_from_window_and_output(
    window: list[Message], output: Any
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for msg in window:
        if msg.role != "model":
            continue
        for p in msg.parts:
            if isinstance(p, FunctionCallPart):
                tools.append({"name": p.name, "args": p.args})
    for p in normalize_output_to_parts(output):
        if isinstance(p, FunctionCallPart):
            tools.append({"name": p.name, "args": p.args})
    return tools


class RejectWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate each run so curation sees only this run's rejects (not stale history).
        self._fh = self.path.open("w", encoding="utf-8")
        self.count = 0

    def write(
        self,
        *,
        trace_id: str,
        turn: dict[str, Any] | None,
        reason: str,
        needs_review: bool = False,
        excluded: bool = False,
        partial: dict[str, Any] | None = None,
    ) -> None:
        attrs = _attrs(turn) if turn else {}
        record = {
            "source_trace_id": trace_id,
            "turn_observation_id": turn.get("id") if turn else None,
            "turn_number": attrs.get("turn.number"),
            "reason": reason,
            "needs_review": needs_review,
            "excluded": excluded,
            "partial": partial or {},
        }
        self._fh.write(json.dumps(record, default=_json_default) + "\n")
        self._fh.flush()
        self.count += 1

    def close(self) -> None:
        self._fh.close()


def collapse_vad_duplicate_turns(
    turns_out: list[dict[str, Any]],
    *,
    trace_id: str,
    turn_by_number: dict[int, dict[str, Any]],
    rejects: RejectWriter,
) -> list[dict[str, Any]]:
    """
    Drop earlier turns in each consecutive run of byte-identical user_text.
    VAD can finalize a partial utterance, then re-finalize the full one.
    """
    if len(turns_out) <= 1:
        return turns_out

    collapsed: list[dict[str, Any]] = []
    i = 0
    while i < len(turns_out):
        j = i + 1
        while (
            j < len(turns_out)
            and turns_out[j]["user_text"] == turns_out[i]["user_text"]
        ):
            j += 1

        if j - i > 1:
            keeper = turns_out[j - 1]
            for dropped_turn in turns_out[i : j - 1]:
                rejects.write(
                    trace_id=trace_id,
                    turn=turn_by_number.get(dropped_turn["turn_number"]),
                    reason="vad_duplicate_turn_dropped",
                    needs_review=False,
                    excluded=True,
                    partial={
                        "turn_number": dropped_turn["turn_number"],
                        "collapsed_into_turn_number": keeper["turn_number"],
                        "user_text": dropped_turn["user_text"],
                    },
                )
            collapsed.append(keeper)
        else:
            collapsed.append(turns_out[i])
        i = j

    return collapsed


def extract_conversation(
    trace_id: str,
    observations: list[dict[str, Any]],
    rejects: RejectWriter,
) -> dict[str, Any] | None:
    children_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in observations:
        parent = o.get("parentObservationId")
        if parent:
            children_of[parent].append(o)

    turn_spans = [
        o for o in observations if o.get("type") == "SPAN" and o.get("name") == "turn"
    ]

    ordered: list[tuple[int, dict[str, Any]]] = []
    for t in turn_spans:
        raw = _attrs(t).get("turn.number")
        if raw is None:
            rejects.write(trace_id=trace_id, turn=t, reason="missing_turn_number")
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            rejects.write(
                trace_id=trace_id,
                turn=t,
                reason="missing_turn_number",
                partial={"turn.number_raw": raw},
            )
            continue
        ordered.append((n, t))
    ordered.sort(key=lambda pair: pair[0])

    by_number = [t for _, t in ordered]
    by_time = sorted(by_number, key=lambda o: o.get("startTime") or "")
    if [t.get("id") for t in by_time] != [t.get("id") for t in by_number]:
        warnings.warn(
            f"trace {trace_id}: startTime order != turn.number order",
            stacklevel=2,
        )

    turn_ids = {t["id"] for _, t in ordered}
    turn_by_number = {n: turn for n, turn in ordered}
    conversation_parent = _resolve_conversation_parent(
        observations, turn_spans, trace_id
    )
    conversation_parent_id = (
        conversation_parent["id"] if conversation_parent else None
    )

    turns_out: list[dict[str, Any]] = []

    def _reject_stop_trace(
        *,
        turn_index: int,
        turn: dict[str, Any],
        reason: str,
        needs_review: bool = False,
        partial: dict[str, Any] | None = None,
    ) -> None:
        """Log a turn rejection and stop; remaining turns on this trace are dropped."""
        remaining = ordered[turn_index + 1 :]
        dropped = len(remaining)
        payload = dict(partial or {})
        payload["subsequent_turns_dropped"] = dropped
        if dropped:
            payload["dropped_turn_numbers"] = [tn for tn, _ in remaining]
        reason_out = reason
        if dropped:
            reason_out = f"{reason}; subsequent_turns_dropped={dropped}"
        rejects.write(
            trace_id=trace_id,
            turn=turn,
            reason=reason_out,
            needs_review=needs_review,
            partial=payload,
        )

    for i, (n, turn) in enumerate(ordered):
        kids = children_of.get(turn["id"], [])
        stt_kids = sorted(
            [k for k in kids if k.get("name") == "stt"],
            key=lambda x: x.get("startTime") or "",
        )
        llm_kids = sorted(
            [k for k in kids if k.get("name") == "llm"],
            key=lambda x: x.get("startTime") or "",
        )

        # First ordered turn with no llm = scripted opening greeting (expected),
        # including cases with neither STT nor LLM (no barge-in). Skip before
        # missing_stt so we do not truncate the rest of the trace.
        if i == 0 and not llm_kids:
            rejects.write(
                trace_id=trace_id,
                turn=turn,
                reason="intro_turn_no_llm_skipped",
                needs_review=False,
                excluded=True,
            )
            continue

        if not stt_kids:
            _reject_stop_trace(turn_index=i, turn=turn, reason="missing_stt")
            break
        if not llm_kids:
            orphan = find_orphan_llm_in_turn_window(
                observations,
                conversation_parent_id=conversation_parent_id,
                turn=turn,
                turn_ids=turn_ids,
            )
            orphan_partial: dict[str, Any] = {
                "orphan_llm_candidate_found": orphan is not None,
            }
            if orphan is not None:
                orphan_partial["orphan_llm_candidate_id"] = orphan.get("id")
            _reject_stop_trace(
                turn_index=i,
                turn=turn,
                reason="missing_llm",
                partial=orphan_partial,
            )
            break

        transcripts: list[str] = []
        for s in stt_kids:
            text = _normalize_transcript(_attrs(s).get("transcript"))
            if text is not None:
                transcripts.append(text)
        distinct = _dedupe_preserve_order(transcripts)
        if not distinct:
            _reject_stop_trace(turn_index=i, turn=turn, reason="missing_stt")
            break

        needs_review = False
        if len(distinct) > 1:
            user_text = " ".join(distinct)
            needs_review = True
            rejects.write(
                trace_id=trace_id,
                turn=turn,
                reason="multi_distinct_stt_joined",
                needs_review=True,
                excluded=False,
                partial={
                    "user_text": user_text,
                    "all_transcripts": distinct,
                },
            )
        else:
            user_text = distinct[0]

        text_chunks: list[str] = []
        try:
            for llm in llm_kids:
                chunk = assistant_text_from_llm_output(llm.get("output"))
                if chunk.strip():
                    text_chunks.append(chunk.strip())
        except ParseError:
            _reject_stop_trace(turn_index=i, turn=turn, reason="unparseable_llm_io")
            break
        expected_assistant_text = " ".join(text_chunks)

        last_llm = llm_kids[-1]
        try:
            messages = parse_messages(last_llm.get("input"))
        except ParseError:
            _reject_stop_trace(turn_index=i, turn=turn, reason="unparseable_llm_io")
            break

        slice_start = None
        for idx in range(len(messages) - 1, -1, -1):
            m = messages[idx]
            if m.role == "user" and has_text_part(m):
                slice_start = idx
                break
        if slice_start is None:
            _reject_stop_trace(
                turn_index=i,
                turn=turn,
                reason="no_user_text_message_in_llm_input",
            )
            break

        window = messages[slice_start:]
        try:
            expected_tool_calls = tool_calls_from_window_and_output(
                window, last_llm.get("output")
            )
        except ParseError:
            _reject_stop_trace(turn_index=i, turn=turn, reason="unparseable_llm_io")
            break

        turn_entry: dict[str, Any] = {
            "turn_number": n,
            "user_text": user_text,
            "expected_assistant_text": expected_assistant_text,
            "expected_tool_calls": expected_tool_calls,
        }
        if needs_review:
            turn_entry["needs_review"] = True
        turns_out.append(turn_entry)

    if not turns_out:
        return None

    turns_out = collapse_vad_duplicate_turns(
        turns_out,
        trace_id=trace_id,
        turn_by_number=turn_by_number,
        rejects=rejects,
    )
    if not turns_out:
        return None

    return {
        "conversation_id": conversation_id_for_trace(trace_id),
        "source_trace_id": trace_id,
        "session_id": session_id_from_observations(observations),
        "ani": synthetic_ani_for_trace(trace_id),
        "tags": [],
        "turns": turns_out,
    }


def extract_conversations(
    client: Langfuse,
    trace_ids: list[str],
    rejects: RejectWriter,
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for trace_id in trace_ids:
        observations = fetch_all_observations_v2(client, trace_id=trace_id)
        doc = extract_conversation(trace_id, observations, rejects)
        if doc:
            docs.append(doc)
    return docs


def _build_client() -> Langfuse:
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_BASE_URL")
    missing = [
        name
        for name, value in (
            ("LANGFUSE_PUBLIC_KEY", public_key),
            ("LANGFUSE_SECRET_KEY", secret_key),
            ("LANGFUSE_BASE_URL", host),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "missing required env var(s): " + ", ".join(missing)
        )
    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
        timeout=60,
    )


def load_conversation_docs(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    docs: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        docs.append(json.loads(line))
    return docs


def merge_conversation_docs(
    existing: list[dict[str, Any]],
    new_docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Merge by conversation_id: new entries replace matching existing ones;
    unmatched existing entries are kept; new-only entries are appended.
    """
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for doc in existing:
        cid = doc.get("conversation_id")
        if not cid:
            continue
        if cid not in by_id:
            order.append(cid)
        by_id[cid] = doc
    for doc in new_docs:
        cid = doc.get("conversation_id")
        if not cid:
            continue
        if cid not in by_id:
            order.append(cid)
        by_id[cid] = doc
    return [by_id[cid] for cid in order]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract eval dataset conversations from Langfuse v2 observations."
    )
    parser.add_argument(
        "--trace-id",
        action="append",
        dest="trace_ids",
        required=True,
        help="Langfuse trace id (repeatable).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output JSONL path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--rejects",
        type=Path,
        default=DEFAULT_REJECTS,
        help=f"Reject JSONL path (default: {DEFAULT_REJECTS})",
    )
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")
    client = _build_client()

    rejects = RejectWriter(args.rejects)
    try:
        docs = extract_conversations(client, args.trace_ids, rejects)
    finally:
        rejects.close()

    existing = load_conversation_docs(args.out)
    merged = merge_conversation_docs(existing, docs)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for doc in merged:
            fh.write(json.dumps(doc, default=_json_default) + "\n")

    replaced = sum(
        1
        for d in docs
        if any(e.get("conversation_id") == d.get("conversation_id") for e in existing)
    )
    print(
        f"Extracted {len(docs)} conversation(s) this run "
        f"({replaced} replaced, {len(docs) - replaced} new)\n"
        f"Merged dataset: {len(merged)} conversation(s) → {args.out}\n"
        f"Rejects this run: {rejects.count} → {args.rejects}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
