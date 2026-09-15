"""Google Cloud TTS factory for the voice pipeline."""

import os
from pipecat.services.google.tts import GoogleTTSService
from pipecat.transcriptions.language import Language


def get_tts_service() -> GoogleTTSService:
    """Configures and returns the Google Cloud Text-to-Speech service."""
    return GoogleTTSService(
        settings=GoogleTTSService.Settings(
            voice=os.getenv("GOOGLE_TTS_VOICE_ID"),
            language=Language.EN_US,
        ),
    )
