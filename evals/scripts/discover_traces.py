#!/usr/bin/env python3
"""
Discover twilio_voice_session trace IDs via Langfuse v2 observations.

Fetches observations in a time/environment window (paginated to exhaustion),
groups by traceId, and keeps traces whose root observation is named
twilio_voice_session. Prints a reviewable ID list only — does not extract.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from langfuse import Langfuse

from _langfuse_utils import fetch_all_observations_v2

# Discovery window (inclusive-style bounds passed to get_many).
FROM_START_TIME = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
TO_START_TIME = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
ENVIRONMENT = "production"
ROOT_NAME = "twilio_voice_session"

# Lightweight fields for discovery (no io needed).
DISCOVERY_FIELDS = "core,basic,time,trace_context"


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
        raise SystemExit("missing required env var(s): " + ", ".join(missing))
    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
        timeout=120,
    )


def _is_root(obs: dict[str, Any]) -> bool:
    if obs.get("isRootObservation") is True:
        return True
    return obs.get("parentObservationId") is None


def _root_for_group(obs_list: list[dict[str, Any]]) -> dict[str, Any] | None:
    roots = [o for o in obs_list if _is_root(o)]
    if not roots:
        return None
    # Prefer explicit isRootObservation when multiple candidates exist.
    flagged = [o for o in roots if o.get("isRootObservation") is True]
    return flagged[0] if flagged else roots[0]


def discover_twilio_voice_trace_ids(
    client: Langfuse,
) -> tuple[list[str], dict[str, int]]:
    print(
        "Fetching v2 observations: "
        f"from_start_time={FROM_START_TIME.isoformat()} "
        f"to_start_time={TO_START_TIME.isoformat()} "
        f"environment={ENVIRONMENT!r}"
    )
    observations = fetch_all_observations_v2(
        client,
        fields=DISCOVERY_FIELDS,
        from_start_time=FROM_START_TIME,
        to_start_time=TO_START_TIME,
        environment=ENVIRONMENT,
    )

    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in observations:
        tid = o.get("traceId")
        if tid:
            by_trace[tid].append(o)

    qualifying: list[str] = []
    no_root = 0
    for tid, group in by_trace.items():
        root = _root_for_group(group)
        if root is None:
            no_root += 1
            continue
        if root.get("name") == ROOT_NAME:
            qualifying.append(tid)

    qualifying.sort()
    stats = {
        "total_observations": len(observations),
        "distinct_trace_groups": len(by_trace),
        "groups_without_root": no_root,
        "passed_name_filter": len(qualifying),
    }
    return qualifying, stats


def main() -> int:
    load_dotenv()
    # Script dir is on sys.path when run as a file; ensure .env from repo root.
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    load_dotenv(os.path.join(repo_root, ".env"))

    client = _build_client()
    trace_ids, stats = discover_twilio_voice_trace_ids(client)

    print()
    print("SUMMARY")
    print(f"  total observations fetched : {stats['total_observations']}")
    print(f"  distinct trace groups      : {stats['distinct_trace_groups']}")
    print(f"  groups without root        : {stats['groups_without_root']}")
    print(f"  passed name filter         : {stats['passed_name_filter']}")
    print(f"    (root name == {ROOT_NAME!r})")
    print()
    print("QUALIFYING TRACE IDS")
    if not trace_ids:
        print("  (none)")
    else:
        for tid in trace_ids:
            print(f"  {tid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
