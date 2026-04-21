"""Google Gemini LLM factory for the voice pipeline."""

import os
from pipecat.services.google.llm import GoogleLLMService


def get_llm_service() -> GoogleLLMService:
    """Configures and returns the Gemini Language Model service."""
    return GoogleLLMService(
        api_key=os.getenv("GEMINI_API_KEY"),
        settings=GoogleLLMService.Settings(
            model="gemini-2.5-flash",
            temperature=0.3,
            max_tokens=150,  # Keeps responses brief for phone calls
        ),
    )