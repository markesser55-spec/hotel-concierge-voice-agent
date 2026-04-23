"""Deepgram STT factory for the voice pipeline (telephony-oriented defaults)."""

import asyncio
import os
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transcriptions.language import Language


class ResilientDeepgramSTTService(DeepgramSTTService):
    """
    Fixes a deadlock where the pipeline freezes permanently after a WebSocket
    drop that occurs mid-finalize.

    Root cause:
      1. User stops speaking → request_finalize() sets _finalize_requested=True
      2. send_finalize() fires over the WebSocket
      3. WebSocket drops before Deepgram echoes back from_finalize=True
      4. confirm_finalize() is never called → _finalize_requested stays True
      5. Pipecat reconnects cleanly, but the stale flag blocks all future
         TranscriptionFrames from being emitted → permanent freeze.

    Fix:
      Override _connection_handler to reset both finalize flags whenever a
      connection drops, before the retry loop begins. This is safe because
      the old connection is already gone — there is nothing left to finalize.
    """

    async def _connection_handler(self):
        while True:
            try:
                await super()._connection_handler()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Connection dropped mid-finalize. Reset stale flags so the
                # pipeline doesn't wait forever for a confirmation that will
                # never arrive on the new connection.
                self._finalize_requested = False
                self._finalize_pending = False


def get_stt_service() -> ResilientDeepgramSTTService:
    """Configures and returns the Deepgram Speech-to-Text service."""
    return ResilientDeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        should_interrupt=False,  # Silero VAD + aggregator already handle interruptions
        settings=DeepgramSTTService.Settings(
            model=os.getenv("DEEPGRAM_MODEL", "nova-2-phonecall"),  # 🚀 Upgraded for Cellular!
            language=Language.EN_US,
            punctuate=True,
            interim_results=False,
            endpointing=False,
            vad_events=False,
        ),
    )