"""Deepgram STT factory for the voice pipeline (telephony-oriented defaults)."""

import os
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transcriptions.language import Language


def get_stt_service() -> DeepgramSTTService:
    """Configures and returns the Deepgram Speech-to-Text service."""
    return DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        should_interrupt=False,  # Silero VAD + aggregator already handle interruptions
        settings=DeepgramSTTService.Settings(
            model=os.getenv("DEEPGRAM_MODEL", "nova-2-phonecall"), # 🚀 Upgraded for Cellular!
            language=Language.EN_US,
            punctuate=True,
            interim_results=True,
            endpointing=300,
            vad_events=False,
        ),
    )