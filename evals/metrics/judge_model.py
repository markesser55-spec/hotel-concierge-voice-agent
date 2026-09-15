"""Shared DeepEval judge LLM for eval metrics."""

from __future__ import annotations

import os

from deepeval.models import GeminiModel

JUDGE_MODEL_NAME = "gemini-2.5-flash"


def get_judge_model() -> GeminiModel:
    """
    Return the shared Gemini judge model for DeepEval metrics.

    Uses GEMINI_API_KEY (same env var as the production agent). Temperature is
    intentionally unset so DeepEval defaults to 0.0 for deterministic scoring.
    """
    return GeminiModel(
        model=JUDGE_MODEL_NAME,
        api_key=os.getenv("GEMINI_API_KEY"),
    )
