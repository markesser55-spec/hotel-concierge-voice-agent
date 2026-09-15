"""
Shared Langfuse v2 observations helpers for evals/scripts.

Uses client.api.observations.get_many (GET /api/public/v2/observations).
"""

from __future__ import annotations

from typing import Any

from langfuse import Langfuse

# Field groups needed for STT transcript / LLM io / TTS text / turn attrs.
DEFAULT_V2_FIELDS = (
    "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context"
)


def to_dict(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True)
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


def fetch_all_observations_v2(
    client: Langfuse,
    *,
    fields: str = DEFAULT_V2_FIELDS,
    limit: int = 100,
    **filters: Any,
) -> list[dict[str, Any]]:
    """
    Paginate get_many until the cursor is exhausted.

    ``filters`` are forwarded as keyword args to get_many (e.g. trace_id,
    from_start_time, to_start_time, environment, name, type). ``fields``,
    ``limit``, and ``cursor`` are owned by this helper and must not be passed
    via ``filters``.
    """
    reserved = {"fields", "limit", "cursor"}
    overlap = reserved & set(filters)
    if overlap:
        raise TypeError(
            "fetch_all_observations_v2 owns pagination kwargs; "
            f"do not pass {sorted(overlap)} via filters"
        )

    all_obs: list[dict[str, Any]] = []
    cursor = None
    while True:
        resp = client.api.observations.get_many(
            fields=fields,
            limit=limit,
            cursor=cursor,
            **filters,
        )
        payload = to_dict(resp)
        batch = list(payload.get("data") or [])
        all_obs.extend(batch)
        meta = payload.get("meta") or {}
        cursor = meta.get("cursor")
        if not cursor or not batch:
            break
    return all_obs
