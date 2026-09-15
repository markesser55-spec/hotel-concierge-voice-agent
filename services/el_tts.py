"""ElevenLabs TTS factory for the voice pipeline."""

import os
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService


def get_tts_service() -> ElevenLabsTTSService:
    """Configures and returns the ElevenLabs Text-to-Speech service."""
    return ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID"),
            model="eleven_turbo_v2_5",  # Important for low latency on phone calls
        ),
    )
