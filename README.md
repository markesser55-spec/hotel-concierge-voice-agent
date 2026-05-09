# Enterprise Voice AI — Hotel Concierge (Capstone)

## Project Overview

This project is a **production-oriented telephony concierge** for a hotel use case. Inbound calls arrive via **Twilio**, **`POST /inbound-call`** returns TwiML, and **`WebSocket /ws`** carries **Twilio Media Streams** into a **FastAPI** + **Pipecat** realtime voice pipeline:

- **Speech-to-text** (Deepgram) transcribes the caller.
- **Voice activity detection** (Silero via Pipecat) and configurable turn-taking support natural dialogue on noisy phone lines.
- **Google Gemini** handles reasoning and tools; **ElevenLabs** streams TTS back at telephony-friendly sample rates.

**Architecture today is split:**

| Process | Role | Default |
|--------|------|--------|
| **Voice gateway** (`server.py`) | Twilio HTTP + WebSocket, Pipecat **PipelineRunner**, per-call tracing, Loguru context (`call_sid`, `ani`, `dnis`). | **`PORT`** (often **8000** locally, **8080** on Cloud Run) |
| **Data MCP server** (`mcp_server.py`) | **Supabase** (guests/reservations + vector RAG for policies), **Google Places** nearby search; exposes tools over **SSE**. | **`http://127.0.0.1:8001/sse`** (same host as voice when MCP runs as a **sidecar**) |

The tier-1 **`tools.py`** module is an **MCP client**: it forwards DB/RAG/maps work to MCP via SSE instead of importing `database.py` directly. **`auth.py`** wires **Twilio Verify** for SMS OTP before sensitive reservation flows (`TWILIO_VERIFY_SERVICE_SID`). Tools are created per call with **`get_hotel_concierge_tools(ani)`**.

**Reservation mutations** use a **second Gemini call** inside **`route_to_reservation_specialist`** (structured **`SpecialistDecision`** + MCP **`db_modify_reservation`**) after the guest is verified—an orchestrator / specialist pattern, not a separate long-running agent for every tool.

**Observability:** Optional **Langfuse** via **OpenTelemetry** OTLP/HTTP (`setup_tracing` + **`PipelineTask(enable_tracing=True)`**). Logs and spans use **PII scrubbing** for ANI / E.164 patterns where configured in `server.py`.

**Offline QA:** **`judge/qa_judge.py`** parses Pipecat logs or **Google Cloud Run JSON** exports and grades transcripts with Gemini (**LLM-as-judge**). Optional RTF support with `striprtf`.

The **Dockerfile** is used by **Google Cloud Build** to produce the image referenced by **`service.yaml`** for **Cloud Run** (voice + MCP sidecar). **Local testing does not use Docker**—run **`mcp_server.py`** and **`server.py`** in two terminals (see below). Deeper design notes live in **`ARCHITECTURE.md`**.

---

## Tech Stack

| Layer | Technology |
|--------|------------|
| API & WebSockets | **FastAPI**, **Uvicorn** |
| Voice orchestration | **Pipecat** (Twilio serializer, Silero VAD, metrics, tracing hooks) |
| Telephony & auth | **Twilio** (TwiML + Media Streams); **Twilio Verify** (SMS PIN) |
| Speech-to-text | **Deepgram** (`nova-2-phonecall` by default via `services/stt.py`) |
| LLM | **Google Gemini** (`google-genai`) |
| Text-to-speech | **ElevenLabs** |
| Data & RAG | **Supabase** (Postgres + `match_hotel_policies` RPC), used inside **MCP** |
| Places | **Google Places** HTTP API (**MCP** tool) |
| Tooling bridge | **`mcp`** (FastMCP server + SSE client) |
| Schemas | **Pydantic** |
| Logging & traces | **Loguru**; **OpenTelemetry** → optional **Langfuse** |
| Runtime | **Python 3.12** locally; **Dockerfile** + **Cloud Build** for deployable images |

---

## Repository map

| Path | Purpose |
|------|---------|
| `server.py` | FastAPI routes, Twilio handshake, telemetry bootstrap, Pipecat run |
| `pipeline.py` | STT/VAD/LLM/TTS ordering, idle handling, **`enable_tracing=True`** |
| `tools.py` | MCP client + per-call tools (Verify, lookups, Tier-3 specialist for mutations) |
| `mcp_server.py` | MCP SSE server (**8001`): `db_*`, `search_hotel_policies`, `search_nearby_places` |
| `database.py` | Supabase access (loaded by MCP server for data tools) |
| `auth.py` | Twilio Verify send/check PIN |
| `services/` | Deepgram / ElevenLabs / Gemini factories |
| `prompts.py`, `models.py` | Prompts and validation models |
| `judge/qa_judge.py` | Automated QA from exported logs |
| `service.yaml` | **Cloud Run** Knative service manifest: **multi-container** (voice + MCP sidecar), env, **`secretKeyRef`**, **`container-dependencies`**, probes—**sanitize** before sharing publicly |

---

## Local development (full stack)

Local testing uses **two terminals** (no Docker required):

1. **`pip install -r requirements.txt`** and configure **`.env`** in the project root.
2. **Terminal A — MCP data server** (required for DB / RAG / Maps tools):

   ```bash
   python mcp_server.py
   ```

3. **Terminal B — voice gateway:**

   ```bash
   python server.py
   ```

   Equivalent: `uvicorn server:app --host 0.0.0.0 --port 8000`.

**Important:** **`tools.call_mcp_tool`** targets **`http://127.0.0.1:8001/sse`**. On Cloud Run, MCP runs as a **sidecar** in the same instance so that URL still works; if you split MCP to another host, update **`tools.py`** accordingly.

**Twilio:** Use a tunnel (e.g. **ngrok**) so Twilio can reach **`https://<public-host>/inbound-call`** and **`wss://<public-host>/ws`**.

---

## Environment Variable Setup

> **Security warning**  
> **Never commit real API keys.** Keep **`.env`**, **`env.yaml`**, and secrets **local-only**. Use placeholders such as **`your_key_here`**. Prefer **Secret Manager** (or equivalent) in production Cloud Run.

**Local vs. cloud**

- **Local** — **`.env`** with **`KEY="value"`** (dotenv); loaded automatically by **`python-dotenv`** in the app modules.
- **Cloud Run** — Environment and secrets are declared in **`service.yaml`** (plain **`env.value`** for non-secrets, **`valueFrom.secretKeyRef`** for Secret Manager). You can still maintain a private **`env.yaml`** or docs for non-GCP workflows, but deploys follow **`service.yaml`**.

Treat **`env.yaml`** like **`.env`**: keep it **out of git** (this repo lists both in **`.gitignore`**) and never bake secrets into **`service.yaml`** literals. **`.dockerignore`** excludes **`.env`**, **`env.yaml`**, and **`service.yaml`** so **`docker build`** does not copy deploy secrets into the image.

### Voice + shared

| Variable | Purpose |
|----------|---------|
| `DEEPGRAM_API_KEY` | **Required** on voice server; websocket closes if missing. |
| `GEMINI_API_KEY` | Gemini (`google-genai`). |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | TTS. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | REST + Pipecat hang-up / serializer behavior. |
| `TWILIO_VERIFY_SERVICE_SID` | **Twilio Verify** service SID (`auth.py`). |
| `ENVIRONMENT` | `production` → JSON logs; omit or use `development` for colored Loguru locally. |
| `PORT` | Listen port (**8000** default locally; Cloud Run sets this). |

### Langfuse (optional)

| Variable | Purpose |
|----------|---------|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Basic-auth material for OTLP. |
| `LANGFUSE_BASE_URL` | e.g. `https://cloud.langfuse.com` (code appends **`/api/public/otel/v1/traces`**). |

### MCP / data layer

Load these wherever **`mcp_server.py`** runs (usually the same `.env` on a dev machine):

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase client for DB + vector RAG tools. |
| `GOOGLE_MAPS_API_KEY` | Places (**MCP**). |

Optional:

| Variable | Purpose |
|----------|---------|
| `DEEPGRAM_MODEL` | Override STT model. |

Placeholder **`.env`** fragment:

```dotenv
DEEPGRAM_API_KEY="your_key_here"
GEMINI_API_KEY="your_key_here"
ELEVENLABS_API_KEY="your_key_here"
ELEVENLABS_VOICE_ID="your_voice_id_here"
TWILIO_ACCOUNT_SID="your_account_sid_here"
TWILIO_AUTH_TOKEN="your_auth_token_here"
TWILIO_VERIFY_SERVICE_SID="your_verify_service_sid_here"
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_KEY="your_key_here"
GOOGLE_MAPS_API_KEY="your_key_here"
LANGFUSE_PUBLIC_KEY="your_key_here"
LANGFUSE_SECRET_KEY="your_key_here"
LANGFUSE_BASE_URL="https://cloud.langfuse.com"
ENVIRONMENT="production"
```

---

## Dockerfile (Cloud Build only)

The **multi-stage** **`Dockerfile`** is for **Cloud Build** and **Cloud Run**, not for local development:

1. **Builder** — `python:3.12-slim` + **`build-essential`**, **`venv`** at **`/opt/venv`**, **`pip install -r requirements.txt`**.
2. **Runtime** — Slim image + **`ffmpeg`**; copies **`/opt/venv`** and app code; default **`CMD`** runs **`uvicorn server:app`**.

On Cloud Run, **`service.yaml`** runs **two containers** from this image: the **voice** container uses the default **`CMD`**; the **MCP** sidecar overrides **`command`** to **`python mcp_server.py`**.

---

## Google Cloud Run deployment

**1. Build and push the image** (Artifact Registry tag must match what **`service.yaml`** references):

```bash
gcloud builds submit --tag us-west4-docker.pkg.dev/gen-lang-client-0063140545/cloud-run-source-deploy/hotel-concierge-voice-agent:latest .
```

**2. Apply the Cloud Run service** from the repo root:

```bash
gcloud run services replace service.yaml --region us-west4
```

**Checklist**

- **`service.yaml`** — Multi-container service (**voice-agent** + **mcp-server** sidecar), **`run.googleapis.com/container-dependencies`**, env, **`secretKeyRef`** for Secret Manager, scaling, ingress, etc. Edit **image**, **region**, **service account**, and secret names for your project.
- Create secrets in **Secret Manager** and grant the Cloud Run runtime service account **Secret Accessor** on each secret referenced in the manifest.
- Avoid long-lived **plaintext secrets** in **`service.yaml`**; prefer **`secretKeyRef`** (and rotate anything ever committed in plain `value:` fields).
- Point Twilio's voice URL at **`https://YOUR_SERVICE_URL/inbound-call`**. Set **`ENVIRONMENT=production`** in **`service.yaml`** (or equivalent) for JSON logs in **Cloud Logging**.
- On the instance loopback, **`tools.py`** can keep **`http://127.0.0.1:8001/sse`** while MCP is a **sidecar**. If MCP moves to another host, change that URL in code.

**Forks:** substitute your **GCP project**, **region**, and **Artifact Registry** path in the **`gcloud builds submit --tag`** argument.

---

## Automated QA (optional)

```bash
python judge/qa_judge.py path/to/log_or_gcp_json_export.txt
```

Install **`striprtf`** optionally for RTF log files from macOS TextEdit.

---

## License

Add your organization’s license terms here if applicable.
