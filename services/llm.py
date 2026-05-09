"""Google Gemini LLM factory for the voice pipeline."""

import os
from pipecat.services.google.llm import GoogleLLMService

def get_llm_service() -> GoogleLLMService:
    """Configures and returns the Tier 1 Gemini Language Model service."""
    return GoogleLLMService(
        api_key=os.getenv("GEMINI_API_KEY"),
        settings=GoogleLLMService.Settings(
            # TIER 1: Ultra-fast front-line gateway for conversational routing
            model="gemini-2.5-flash",
            temperature=0.3,
            max_tokens=150,
        ),
    )