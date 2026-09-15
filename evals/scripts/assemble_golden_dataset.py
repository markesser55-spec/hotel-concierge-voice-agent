#!/usr/bin/env python3
"""
Assemble golden_v1.jsonl from raw Langfuse extractions + curation decisions.

Reads:
  - evals/datasets/from_langfuse.jsonl  (raw extracted conversations)
  - evals/datasets/curation.json        (include/tags/per-turn notes)

Writes:
  - evals/datasets/golden_v1.jsonl

Never modifies the input files. Safe to re-run (idempotent overwrite of output).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW = REPO_ROOT / "evals" / "datasets" / "from_langfuse.jsonl"
DEFAULT_CURATION = REPO_ROOT / "evals" / "datasets" / "curation.json"
DEFAULT_OUT = REPO_ROOT / "evals" / "datasets" / "golden_v1.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    if not path.exists():
        return docs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        docs.append(json.loads(line))
    return docs


def load_curation(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"curation.json must be an object keyed by conversation_id, got {type(data).__name__}")
    return data


def apply_curation(
    conversation: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Return a deep copy with curation tags and per-turn notes applied."""
    out = copy.deepcopy(conversation)
    tags = entry.get("tags")
    out["tags"] = list(tags) if isinstance(tags, list) else []

    notes = entry.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}

    turns = out.get("turns") or []
    if not isinstance(turns, list):
        turns = []
    by_number = {
        t.get("turn_number"): t
        for t in turns
        if isinstance(t, dict) and t.get("turn_number") is not None
    }
    for raw_key, note_text in notes.items():
        try:
            turn_number = int(raw_key)
        except (TypeError, ValueError):
            continue
        turn = by_number.get(turn_number)
        if turn is None:
            continue
        turn["notes"] = note_text

    out["turns"] = turns
    return out


def assemble(
    raw_docs: list[dict[str, Any]],
    curation: dict[str, Any],
) -> list[dict[str, Any]]:
    golden: list[dict[str, Any]] = []
    for doc in raw_docs:
        cid = doc.get("conversation_id")
        if not cid or cid not in curation:
            continue
        entry = curation[cid]
        if not isinstance(entry, dict) or not entry.get("include"):
            continue
        golden.append(apply_curation(doc, entry))
    return golden


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble golden_v1.jsonl from from_langfuse.jsonl + curation.json."
    )
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--curation", type=Path, default=DEFAULT_CURATION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if not args.raw.exists():
        print(f"missing raw dataset: {args.raw}", file=sys.stderr)
        return 1
    if not args.curation.exists():
        print(f"missing curation file: {args.curation}", file=sys.stderr)
        return 1

    raw_docs = load_jsonl(args.raw)
    curation = load_curation(args.curation)
    golden = assemble(raw_docs, curation)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for doc in golden:
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")

    included_ids = {d["conversation_id"] for d in golden}
    noted = sum(
        1
        for d in golden
        for t in d.get("turns") or []
        if isinstance(t, dict) and "notes" in t
    )
    print(
        f"Raw conversations: {len(raw_docs)}\n"
        f"Curation entries: {len(curation)}\n"
        f"Included (include:true + present in raw): {len(golden)} → {args.out}\n"
        f"Turns with curation notes: {noted}"
    )
    # Sanity: inputs untouched (mtime check not needed; we never open them for write).
    _ = included_ids
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
