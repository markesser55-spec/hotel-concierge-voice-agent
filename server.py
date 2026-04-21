"""
Hotel Concierge Telephony Server
Handles Twilio routing and WebSocket audio transport.
"""

import os
import sys
import uvicorn
from loguru import logger
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from twilio.twiml.voice_response import VoiceResponse, Connect
from dotenv import load_dotenv

# Pipecat Transports & Runners
from pipecat.pipeline.runner import PipelineRunner
from pipecat.frames.frames import EndFrame, TTSSpeakFrame
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.serializers.twilio import TwilioFrameSerializer

# Import our decoupled architecture
from pipeline import build_pipeline
from prompts import GREETING_PROMPT

load_dotenv()

# ==============================================================================
# 🚀 ENTERPRISE GLOBAL TRACING & OBSERVABILITY
# ==============================================================================
logger.remove() # Remove default logger

# Check if we are running in the cloud (We will set this env var in Docker)
IS_PRODUCTION = os.getenv("ENVIRONMENT") == "production"

if IS_PRODUCTION:
    # ☁️ CLOUD STANDARD: Structured JSON Logging for Google Cloud
    logger.add(sys.stdout, serialize=True)
else:
    # 💻 LOCAL STANDARD: Beautiful Color-Coded Terminal Output
    logger.add(
        sys.stdout, 
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>[{extra[call_sid]}]</cyan> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )

# Set the default contextual fallback so the app doesn't crash on boot
logger.configure(extra={"call_sid": "SYSTEM", "ani": "SYSTEM", "dnis": "SYSTEM"})
# ==============================================================================

app = FastAPI()

@app.post("/inbound-call")
async def handle_incoming_call(request: Request):
    """Twilio hits this HTTP endpoint. We return TwiML telling Twilio to open a WebSocket."""
    # 🚀 Extract ANI and DNIS directly from Twilio's HTTP form
    form_data = await request.form()
    ani = form_data.get("From", "Unknown")
    dnis = form_data.get("To", "Unknown")
    call_sid = form_data.get("CallSid", "Unknown")

    logger.info(f"Incoming call detected! CallSid: {call_sid} | ANI: {ani} | DNIS: {dnis}")
    
    host = request.url.hostname
    response = VoiceResponse()
    connect = Connect()
    
    # 🚀 Inject phone numbers as XML custom parameters
    stream = connect.stream(url=f"wss://{host}/ws", track="inbound_track")
    stream.parameter(name="ani", value=ani)
    stream.parameter(name="dnis", value=dnis)
    response.append(connect)

    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """The actual Pipecat Streaming Audio Pipeline."""
    await websocket.accept()
    logger.info("WebSocket Connected. Waiting for Twilio Start Event...")

    if not os.getenv("DEEPGRAM_API_KEY"):
        logger.error("[ERROR] DEEPGRAM_API_KEY is missing — STT will never produce text.")
        await websocket.close(code=4401)
        return

    # Wait for Twilio Media Streams "start" (streamSid/callSid live under `start`, not only at root).
    stream_sid = None
    call_sid = "Unknown"
    account_sid = None
    ani = "Unknown"
    dnis = "Unknown"

    while not stream_sid:
        data = await websocket.receive_json()
        if data.get("event") == "start":
            start = data.get("start") or {}
            stream_sid = start.get("streamSid") or data.get("streamSid")
            call_sid = start.get("callSid")
            account_sid = start.get("accountSid") or os.getenv("TWILIO_ACCOUNT_SID")

            # 🚀 Extract the smuggled phone numbers from the audio stream!
            custom_params = start.get("customParameters", {})
            ani = custom_params.get("ani", "Unknown")
            dnis = custom_params.get("dnis", "Unknown")

    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    # Pipecat defaults auto_hang_up=True, which requires Twilio REST creds to end the call via API.
    if call_sid and account_sid and auth_token:
        twilio_serializer = TwilioFrameSerializer(
            stream_sid=stream_sid,
            call_sid=call_sid,
            account_sid=account_sid,
            auth_token=auth_token,
        )
    else:
        twilio_serializer = TwilioFrameSerializer(
            stream_sid=stream_sid,
            params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
        )

    # 1. Initialize Twilio Audio Transport
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=8000,   # Twilio's sample rate
            audio_out_sample_rate=8000,
            add_wav_header=False,
            serializer=twilio_serializer,
        ),
    )

    # =====================================================================
    # 🚀 THE MAGIC BUBBLE (Context Variables)
    # EVERYTHING running inside this 'with' block automatically inherits 
    # the call_sid. Pipecat's internal logs, tools.py, and 
    # database.py will all instantly have the CallSid attached!
    # =====================================================================
    with logger.contextualize(call_sid=call_sid, ani=ani, dnis=dnis):
        logger.info(f"Session Officially Established! Caller (ANI): {ani}")
        
        task = build_pipeline(transport)
        runner = PipelineRunner(handle_sigint=False)

        @transport.event_handler("on_client_connected")
        async def on_client_connected(_transport, _client):
            logger.info("Audio Stream Active. Speaking greeting...")
            await task.queue_frames([TTSSpeakFrame(GREETING_PROMPT)])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport, _client):
            logger.info("Twilio Client Disconnected. Call Ended.")
            await task.queue_frames([EndFrame()])

        await runner.run(task)

if __name__ == "__main__":
    # 🚀 CLOUD UPGRADE: Dynamically grab the port from Google Cloud, fallback to 8000 locally!
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"[Booting Hotel Concierge Telephony Server on Port {port}...]")
    
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_graceful_shutdown=10)

