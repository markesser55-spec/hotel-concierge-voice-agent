#!/usr/bin/env python3
"""
ONE-OFF transcript dump — not part of the permanent eval suite.

Replays golden conversations through ConversationAgent + mock tools and
prints golden vs live per turn (no scoring).

Usage:
  python evals/scripts/dump_two_conversation_transcripts.py [conversation_id ...]
  python evals/scripts/dump_two_conversation_transcripts.py --static [id ...]

  IDs may be full UUIDs or unique prefixes (conversation_id or source_trace_id).
  With --static, reads from golden_v1.jsonl then from_langfuse.jsonl; no replay.
  If none are given, dumps the four currently-failing modification-related goldens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

GOLDEN_PATH = REPO_ROOT / "evals" / "datasets" / "golden_v1.jsonl"
FROM_LANGFUSE_PATH = REPO_ROOT / "evals" / "datasets" / "from_langfuse.jsonl"
DEFAULT_IDS = [
    "e21faf1d-41ff-5d1d-bd87-c3c4fdc1982d",
    "88c175b4-e3ff-5577-b328-9b0d19fe3347",
    "673d509a-e447-5610-9830-65c79ba39fe4",
    "9f015f2d-83fa-5c34-80ad-e701ee077c47",
]


def load_dataset_index(path: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    if not path.exists():
        return by_id
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        doc = json.loads(line)
        by_id[doc["conversation_id"]] = doc
    return by_id


def load_combined_index() -> dict[str, dict]:
    combined = load_dataset_index(GOLDEN_PATH)
    for cid, doc in load_dataset_index(FROM_LANGFUSE_PATH).items():
        combined.setdefault(cid, doc)
    return combined


def resolve_ids(requested: list[str], by_id: dict[str, dict]) -> list[str]:
    by_trace = {
        str(doc.get("source_trace_id")): cid
        for cid, doc in by_id.items()
        if doc.get("source_trace_id")
    }
    resolved: list[str] = []
    for req in requested:
        if req in by_id:
            resolved.append(req)
            continue
        if req in by_trace:
            resolved.append(by_trace[req])
            continue
        conv_matches = [cid for cid in by_id if cid.startswith(req)]
        trace_matches = [tid for tid in by_trace if tid.startswith(req)]
        if len(conv_matches) == 1:
            resolved.append(conv_matches[0])
        elif len(trace_matches) == 1:
            resolved.append(by_trace[trace_matches[0]])
        elif not conv_matches and not trace_matches:
            raise SystemExit(f"id not found in golden_v1.jsonl or from_langfuse.jsonl: {req}")
        else:
            raise SystemExit(f"ambiguous prefix {req!r}")
    return resolved


def load_targets(requested: list[str] | None = None) -> list[dict]:
    by_id = load_combined_index()
    ids = resolve_ids(requested or DEFAULT_IDS, by_id)
    return [by_id[cid] for cid in ids]


def dump_static(conversation: dict) -> None:
    cid = conversation["conversation_id"]
    print("=" * 80)
    print(f"conversation_id={cid}")
    print(f"source_trace_id={conversation.get('source_trace_id')}")
    print(f"session_id={conversation.get('session_id')}")
    print(f"tags={conversation.get('tags') or []}")
    print(f"ani={conversation.get('ani')}")
    print("=" * 80)
    for turn in conversation.get("turns") or []:
        n = turn.get("turn_number")
        print(f"\n--- turn {n} ---")
        print(f"user_text: {turn.get('user_text')!r}")
        print(f"expected_assistant_text: {turn.get('expected_assistant_text')!r}")
        print(
            "expected_tool_calls: "
            f"{json.dumps(turn.get('expected_tool_calls') or [], ensure_ascii=False)}"
        )
    print()


def _tool_event_names(tool_events: list) -> list[str]:
    names: list[str] = []
    for ev in tool_events or []:
        if not isinstance(ev, dict):
            continue
        name = ev.get("name")
        if name and name != "unknown":
            names.append(str(name))
    return names


def _lookup_result_statuses(tool_events: list) -> list[str]:
    statuses: list[str] = []
    for ev in tool_events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("name") not in (None, "unknown", "lookup_guest_reservation"):
            # Keep scanning result payloads even when name is unknown.
            pass
        result = ev.get("result")
        if isinstance(result, dict) and "status" in result:
            # Associate status with lookup when name matches or payload has reservation-ish keys
            name = ev.get("name")
            if name == "lookup_guest_reservation" or (
                name in (None, "unknown")
                and ("data" in result or result.get("status") == "needs_auth")
            ):
                statuses.append(str(result.get("status")))
        # Also catch FunctionCallResultFrame-style nesting
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and "status" in parsed:
                statuses.append(str(parsed.get("status")))
    return statuses


def summarize_first_tool_turn(cid: str, turn_number, tool_events: list) -> None:
    names = _tool_event_names(tool_events)
    lookup_statuses = _lookup_result_statuses(tool_events)
    has_verify = "verify_auth_pin" in names
    has_lookup = "lookup_guest_reservation" in names
    print(f"\n>>> AUTH CHECK (first tool-call turn={turn_number}) cid={cid}")
    print(f"    tool names (excl. unknown): {names}")
    print(f"    verify_auth_pin present: {has_verify}")
    print(f"    lookup_guest_reservation present: {has_lookup}")
    print(f"    lookup-related result statuses: {lookup_statuses}")
    if has_lookup and not has_verify:
        if "success" in lookup_statuses and "needs_auth" not in lookup_statuses:
            print(
                "    verdict: lookup returned success with NO prior verify_auth_pin "
                "in this turn's tool_events"
            )
        elif "needs_auth" in lookup_statuses:
            print(
                "    verdict: lookup hit needs_auth; verify_auth_pin not yet called "
                "(gate working for this turn)"
            )
        else:
            print(
                "    verdict: lookup present without verify_auth_pin; "
                f"statuses={lookup_statuses or ['(none parsed)']}"
            )
    elif has_verify and has_lookup:
        print(
            "    verdict: verify_auth_pin appears in the same turn as lookup "
            "(ordering should be inspectable in tool_events above)"
        )
    elif not has_lookup:
        print("    verdict: no lookup_guest_reservation on first tool-call turn")


async def replay(conversation: dict) -> None:
    from agent import ConversationAgent
    from evals.tools import get_eval_hotel_concierge_tools

    cid = conversation["conversation_id"]
    tags = conversation.get("tags") or []
    ani = conversation.get("ani") or "+15550000000"
    print("=" * 80)
    print(f"conversation_id={cid}")
    print(f"tags={tags}")
    print(f"ani={ani}")
    print("=" * 80)

    first_tool_turn_done = False
    async with ConversationAgent(
        ani=ani,
        tool_provider=get_eval_hotel_concierge_tools,
    ) as agent:
        for turn in conversation.get("turns") or []:
            n = turn.get("turn_number")
            user_text = turn.get("user_text") or ""
            print(f"\n--- turn {n} ---")
            print(f"golden user_text: {user_text!r}")
            print(f"golden expected_assistant_text: {turn.get('expected_assistant_text')!r}")
            print(
                "golden expected_tool_calls: "
                f"{json.dumps(turn.get('expected_tool_calls') or [], ensure_ascii=False)}"
            )
            result = await agent.run_turn(user_text)
            print(f"live assistant_text: {result.assistant_text!r}")
            print(
                "live tool_events: "
                f"{json.dumps(result.tool_events, ensure_ascii=False, default=str)}"
            )
            expected = turn.get("expected_tool_calls") or []
            live_events = result.tool_events or []
            if not first_tool_turn_done and (expected or live_events):
                summarize_first_tool_turn(cid, n, live_events)
                first_tool_turn_done = True
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Dump golden conversation transcripts")
    parser.add_argument(
        "--static",
        action="store_true",
        help="Print extracted fields only; no ConversationAgent replay",
    )
    parser.add_argument("ids", nargs="*", help="conversation_id or source_trace_id (prefix ok)")
    args = parser.parse_args()
    requested = args.ids or None
    for conversation in load_targets(requested):
        if args.static:
            dump_static(conversation)
        else:
            await replay(conversation)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
