# Enterprise Voice AI — Hotel Concierge (Capstone)

## Project Overview

This project is a **production-oriented telephony concierge** for a hotel use case. Inbound calls are received via **Twilio**, bridged over **WebSockets** to a **FastAPI** server, and processed by a **Pipecat** real-time voice pipeline:

- **Speech-to-text** (Deepgram) transcribes the caller.
- **Voice activity detection** (Silero via Pipecat) and configurable turn-taking manage natural conversation on noisy phone lines.
- **Large language model** reasoning and tool use (**Google Gemini**) drive concierge behavior: reservations, policy lookup (RAG over embeddings), optional escalation, and nearby-place search (**Google Places**).
- **Text-to-speech** (ElevenLabs) streams responses back to the caller at telephony-friendly sample rates.

Persistent data and semantic search live in **Supabase** (relational tables for guests/reservations and vector search over hotel policy content). The service is containerized for **local Docker** workflows and **Google Cloud Run** deployment, with structured logging in production for observability on Google Cloud.

---

## Tech Stack

| Layer | Technology |
|--------|------------|
| API & WebSockets | **FastAPI**, **Uvicorn** |
| Voice orchestration | **Pipecat** (pipeline, Twilio serializer, metrics) |
| Telephony | **Twilio** (HTTP TwiML + Media Streams WebSocket) |
| Speech-to-text | **Deepgram** (`nova-2-phonecall` by default) |
| LLM & embeddings | **Google Gemini** (`google-genai`) |
| Text-to-speech | **ElevenLabs** |
| Data & vectors | **Supabase** (Postgres + `pgvector`-style RPC for policy RAG) |
| Validation & tooling | **Pydantic**, async HTTP (**aiohttp**) for Google Places |
| Logging | **Loguru** (JSON in production, colored console locally) |
| Runtime | **Python 3.12**, **Docker** |

---

## Environment Variable Setup

> **Security warning — read before configuring anything**  
> **Never commit real API keys, tokens, or secrets** to version control, screenshots, tickets, or public READMEs. Treat `.env`, `env.yaml`, and any local secret files as **local-only**. Use placeholders such as `your_key_here` in documentation and examples. Rotate any credential that may have been exposed. Prefer **Google Secret Manager** (or your organization’s vault) for Cloud Run and **Twilio/Deepgram/ElevenLabs/Supabase** dashboards for key issuance and rotation.

**Local vs. cloud configuration**

- **Local testing** — Use a **`.env`** file in the project root. Each line follows the format `KEY="value"` (dotenv style). The Docker run command can load it with `--env-file .env`.
- **Google Cloud Run** — Use an **`env.yaml`** file for deployment-time variables. Each entry follows the format `KEY: "value"` (YAML mapping). This file is passed to `gcloud run deploy` with `--env-vars-file` (see [Google Cloud Run Deployment](#google-cloud-run-deployment)).

Keep **`env.yaml`** out of the container image and out of git: it must appear in **both** `.gitignore` and `.dockerignore` so secrets are never committed or copied into build context.

Set at minimum:

| Variable | Purpose |
|----------|---------|
| `DEEPGRAM_API_KEY` | **Required** for STT; the WebSocket handler validates presence. |
| `GEMINI_API_KEY` | Gemini LLM access (see also Google GenAI SDK env conventions in your deployment). |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS API key. |
| `ELEVENLABS_VOICE_ID` | Voice identifier for TTS output. |
| `SUPABASE_URL` | Supabase project URL. |
| `SUPABASE_KEY` | Supabase service or anon key with permissions matching your schema. |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID (used for Media Streams and optional hang-up). |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token. If unset with missing SID, the Twilio serializer may run with `auto_hang_up=False`. |
| `GOOGLE_MAPS_API_KEY` | Google Places API (Text Search) for `search_nearby_places` tool. |

Optional:

| Variable | Purpose |
|----------|---------|
| `ENVIRONMENT` | Set to `production` for JSON structured logs to stdout (e.g. Cloud Logging). For local Docker, the example run sets `development` for colored console logs. |
| `PORT` | HTTP port (default `8000` locally; Cloud Run sets this automatically). |
| `DEEPGRAM_MODEL` | Override STT model (default `nova-2-phonecall`). |

Example **placeholder-only** `.env` fragment (do not paste real secrets):

```dotenv
DEEPGRAM_API_KEY="your_key_here"
GEMINI_API_KEY="your_key_here"
ELEVENLABS_API_KEY="your_key_here"
ELEVENLABS_VOICE_ID="your_voice_id_here"
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_KEY="your_key_here"
TWILIO_ACCOUNT_SID="your_account_sid_here"
TWILIO_AUTH_TOKEN="your_auth_token_here"
GOOGLE_MAPS_API_KEY="your_key_here"
ENVIRONMENT="production"
```

---

## Local Docker Execution

**Prerequisites:** Docker installed; a `.env` file with required variables (see above).

From the repository root, build the image:

```bash
docker build -t hotel-concierge-voice-agent .
```

Run the container (interactive TTY, named container, port mapping, env file, and **development** logging override):

```bash
docker run -it --rm --name hotel-concierge-local -p 8000:8000 --env-file .env -e ENVIRONMENT=development hotel-concierge-voice-agent
```

The image defaults `ENVIRONMENT=production` in the Dockerfile; the `-e ENVIRONMENT=development` flag overrides that for local runs so Loguru uses readable colored output instead of JSON. The server listens on `0.0.0.0` with port `${PORT:-8000}`.

**Twilio integration note:** Twilio must reach your `/inbound-call` and `wss://.../ws` endpoints over the public internet. For local Docker, use a tunnel (for example **ngrok**) to expose HTTPS/WSS and configure Twilio’s voice webhook and Media Streams URL to that host.

**Health check:** Once running, the server listens for `POST /inbound-call` (TwiML) and `WebSocket /ws` (audio).

---

## Google Cloud Run Deployment

This project uses **Google Cloud’s Source Deploy** for Cloud Run: you do **not** run `gcloud builds submit` or push images to Artifact Registry by hand. A single command uploads your source from the current directory, **builds the container in Google’s environment**, deploys the service, and applies environment variables from your local `env.yaml`.

```bash
gcloud run deploy hotel-concierge-voice-agent \
  --source . \
  --env-vars-file env.yaml \
  --min-instances 1 \
  --allow-unauthenticated
```

**What this does**

- **Source Deploy (`--source .`)** — Securely sends your application source to Google Cloud; the service builds the production image remotely and rolls out a new revision. No manual Docker tag or registry push is required in your workflow.
- **`--env-vars-file env.yaml`** — Injects the variables defined in `env.yaml` into the Cloud Run service as **runtime environment variables** (available in the running container’s process environment—i.e. loaded for the service, not baked into a public artifact in your repo).
- **`--min-instances 1`** — Keeps at least one instance warm to reduce **cold starts**. That matters for **Twilio**: the first inbound call after idle scale-to-zero can hit high latency or timeouts; a minimum instance improves responsiveness for voice.
- **`--allow-unauthenticated`** — Exposes the HTTPS endpoint without requiring an Identity token for callers (Twilio webhooks need a reachable URL).

**Secrets and `env.yaml`**

- Maintain **`env.yaml`** only on trusted machines; it must remain in **`.gitignore`** and **`.dockerignore`** so it is never committed and is not sent into the Docker build context as part of a careless image build. You still pass it explicitly to `gcloud` at deploy time for variable injection.
- Point Twilio’s **Voice URL** to `https://YOUR_SERVICE_URL/inbound-call` (the app builds `wss://{host}/ws` from the incoming request host).
- With `ENVIRONMENT=production` in `env.yaml`, the app emits structured JSON logs to stdout for **Google Cloud Logging**.

---

## License

Add your organization’s license terms here if applicable.
