"""
Hotel Concierge Telephony Server
Handles Twilio routing and WebSocket audio transport.
"""

import os
import sys
import base64
import logging
import re
import uvicorn
import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.context import Context
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
from pipecat.utils.tracing.setup import setup_tracing

# Import our decoupled architecture
from pipeline import build_pipeline
from prompts import GREETING_PROMPT

load_dotenv()

# ==============================================================================
# DETERMINISTIC SECURITY GUARDRAILS (PII SCRUBBERS)
# ==============================================================================
# The strict pattern for US Phone Numbers
PHONE_REGEX = re.compile(r'\+1\d{10}')

def loguru_pii_scrubber(record):
    """Redact caller ANI and E.164 phone numbers from a Loguru record in-place; preserve DNIS."""
    # Redact ANI by key name
    if "ani" in record["extra"] and record["extra"]["ani"] != "SYSTEM":
        record["extra"]["ani"] = "[REDACTED_ANI]"
    
    # Protect DNIS from the regex before scrubbing message body
    dnis = record["extra"].get("dnis", "")
    message = str(record["message"])
    if dnis and dnis != "SYSTEM":
        message = message.replace(dnis, "KEEP_DNIS")
    message = PHONE_REGEX.sub('[REDACTED_PHONE]', message)
    if dnis and dnis != "SYSTEM":
        message = message.replace("KEEP_DNIS", dnis)
    record["message"] = message

class PIIScrubbingExporter(OTLPSpanExporter):
    """Intercepts OpenTelemetry Spans and redacts ANI but preserves DNIS before transmission."""
    def export(self, spans):
        for span in spans:
            if hasattr(span, "_attributes") and span._attributes:
                for key, value in list(span._attributes.items()):
                    if isinstance(value, str):
                        if key == "ani":
                            span._attributes[key] = "[REDACTED_ANI]"
                        elif key == "dnis":
                            pass  # Always preserve DNIS
                        else:
                            scrubbed = PHONE_REGEX.sub('[REDACTED_PHONE]', value)
                            if scrubbed != value:
                                span._attributes[key] = scrubbed
        return super().export(spans)

# ==============================================================================
# ENTERPRISE GLOBAL TRACING & OBSERVABILITY
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

logger.configure(patcher=loguru_pii_scrubber, extra={"call_sid": "SYSTEM", "ani": "SYSTEM", "dnis": "SYSTEM"})


def configure_observability():
    """Configure Langfuse-backed OpenTelemetry tracing when credentials exist."""

    logging.getLogger("opentelemetry").setLevel(logging.ERROR)

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_BASE_URL")

    if public_key and secret_key:
        auth_str = f"{public_key}:{secret_key}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()

        # Configure Pipecat's OpenTelemetry Exporter to point to Langfuse's trace endpoint
        exporter = PIIScrubbingExporter(
            endpoint=f"{host}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {b64_auth}"}
        )

        # This hooks into all Pipecat services (Deepgram, Gemini, ElevenLabs) automatically!
        setup_tracing(
            service_name="hotel-concierge-v2",
            exporter=exporter
        )

        logger.info("[Enterprise Architect] Langfuse OTel configured WITH PII Scrubbing.")

    else:
        logger.warning("[Enterprise Architect] Langfuse keys missing. Running blind.")

configure_observability()


app = FastAPI()

@app.post("/inbound-call")
async def handle_incoming_call(request: Request):
    """Twilio hits this HTTP endpoint. We return TwiML telling Twilio to open a WebSocket."""
    # Extract ANI and DNIS directly from Twilio's HTTP form
    form_data = await request.form()
    ani = form_data.get("From", "Unknown")
    dnis = form_data.get("To", "Unknown")
    call_sid = form_data.get("CallSid", "Unknown")

    logger.info(f"Incoming call detected! CallSid: {call_sid}")
    
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

            # Extract the smuggled phone numbers from the audio stream!
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
            vad_enabled=False,      # 🛡️ THE FIX: Explicitly disable Pipecat's default transport VAD!
            vad_analyzer=None,
            serializer=twilio_serializer,
        ),
    )

    # =====================================================================
    # EVERYTHING running inside this 'with' block automatically inherits 
    # the call_sid. Pipecat's internal logs, tools.py, and 
    # database.py will all instantly have the CallSid attached!
    # =====================================================================
    with logger.contextualize(call_sid=call_sid, ani=ani, dnis=dnis):
        logger.info(f"Session Officially Established!  Called Number: {dnis}")
        
        task, call_context = build_pipeline(transport, call_sid=call_sid, ani=ani)
        runner = PipelineRunner(handle_sigint=False)

        @transport.event_handler("on_client_connected")
        async def on_client_connected(_transport, _client):
            logger.info("Audio Stream Active. Speaking greeting...")
            await task.queue_frames([TTSSpeakFrame(GREETING_PROMPT)])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport, _client):
            logger.info("Twilio Client Disconnected. Call Ended.")
            await task.queue_frames([EndFrame()])

        tracer = trace.get_tracer("hotel-concierge")
        
        # 'root=True' guarantees a clean trace ID, severing the bleed from previous calls
        with tracer.start_as_current_span(
            "twilio_voice_session", 
            context=Context(), 
            attributes={
                "session.id": call_sid,   # Langfuse natively uses this to group traces into Sessions!
                "call_sid": call_sid,     # Extra searchable tag
                "ani": ani,
                "dnis": dnis
            }
        ):
            # The entire phone call happens inside this execution block!
            await runner.run(task)

            # ==============================================================================
            # 🚀 POST-CALL AGENTIC SWARM TRIGGER
            # ==============================================================================
            try:
                # 1. Dynamically parse the actual Pipecat Context Memory from RAM
                transcript_lines = []
                messages = call_context.get_messages() if hasattr(call_context, "get_messages") else call_context.messages
                
                for msg in messages:
                    # Safely handle Pipecat message objects or dicts
                    msg_dict = msg if isinstance(msg, dict) else (msg.model_dump() if hasattr(msg, "model_dump") else vars(msg))
                    
                    role = str(msg_dict.get("role", "")).upper()
                    if role in ["SYSTEM", ""]:
                        continue # Skip the invisible system prompt
                        
                    speaker = "BOT" if role in ["ASSISTANT", "MODEL"] else "USER"
                    
                    # --- A. EXTRACT TEXT (Handles Universal AND Gemini formats) ---
                    content = msg_dict.get("content", "")
                    if not content and "parts" in msg_dict:
                        content_parts = [p["text"] for p in msg_dict["parts"] if "text" in p]
                        content = " ".join(content_parts)
                            
                    if isinstance(content, str) and content.strip():
                        if "[SYSTEM EVENT]" not in content: # Hide internal nudges
                            transcript_lines.append(f"{speaker}: {content.strip()}")
                            
                    # --- B. EXTRACT TOOL CALLS (Required for QA rubric) ---
                    if "tool_calls" in msg_dict and msg_dict["tool_calls"]:
                        for tc in msg_dict["tool_calls"]:
                            f_name = tc.get("function", {}).get("name", "unknown")
                            transcript_lines.append(f"BOT [ACTION]: Called tool '{f_name}'")
                    elif "parts" in msg_dict:
                        for part in msg_dict.get("parts", []):
                            if "function_call" in part:
                                f_name = part["function_call"].get("name", "unknown")
                                transcript_lines.append(f"BOT [ACTION]: Called tool '{f_name}'")

                    # --- C. EXTRACT TOOL RESPONSES ---
                    if role in ["TOOL", "FUNCTION"]:
                        name = msg_dict.get("name", "unknown")
                        transcript_lines.append(f"SYSTEM [DATA]: Tool '{name}' returned data.")
                    elif "parts" in msg_dict:
                        for part in msg_dict.get("parts", []):
                            if "function_response" in part:
                                name = part["function_response"].get("name", "unknown")
                                transcript_lines.append(f"SYSTEM [DATA]: Tool '{name}' returned data.")

                real_transcript = "\n".join(transcript_lines)
                
                # Fallback if the user hangs up immediately
                if not real_transcript.strip():
                    real_transcript = "USER: [Hung up immediately. No transcript generated.]"
                
                logger.info(f"✅ Live transcript extracted from RAM ({len(real_transcript)} chars).")
                
                # 2. Fire the webhook to the cloud!
                webhook_url = os.getenv("POST_CALL_WEBHOOK_URL", "http://localhost:8002/webhook/process-call")
                
                payload = {
                    "call_sid": call_sid,
                    "guest_phone": ani,
                    "transcript": real_transcript
                }
                
                async with httpx.AsyncClient() as client:
                    logger.info(f"Firing webhook to {webhook_url}...")
                    response = await client.post(webhook_url, json=payload, timeout=30.0)
                    logger.info(f"✅ Webhook delivered. Backend returned: {response.status_code}")
                        
            except httpx.ConnectError:
                logger.warning("⚠️ Webhook Failed: Backend is offline. Gracefully continuing shutdown.")
            except httpx.TimeoutException:
                logger.warning("⏳ Webhook timed out (30s). Voice Agent shutting down gracefully!")
            except Exception as e:
                logger.error(f"❌ Webhook extraction failure: {e}")

        try:
            provider = trace.get_tracer_provider()
            if hasattr(provider, "force_flush"):
                provider.force_flush()
                logger.info("[OpenTelemetry] Traces successfully force-flushed to Langfuse!")
        except Exception as e:
            logger.error(f"Failed to flush telemetry: {e}")

if __name__ == "__main__":
    # CLOUD UPGRADE: Dynamically grab the port from Google Cloud, fallback to 8000 locally!
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"[Booting Hotel Concierge Telephony Server on Port {port}...]")
    
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_graceful_shutdown=10)

