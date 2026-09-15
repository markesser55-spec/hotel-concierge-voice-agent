"""Shared pytest fixtures for the eval suite."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# GEMINI_API_KEY (and Langfuse keys if needed) for ConversationAgent + judge model.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
