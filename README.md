# Kira

Autonomous AI video content generator that works via WhatsApp. Kira researches trending topics, writes scripts, generates AI images and video, produces voiceovers, composes background music, and publishes the final video — all from a single chat message.

Built with **Google ADK (Gemini 3.5 Flash)**, **Fal.ai**, **FFmpeg**, and **Twilio**, deployed on **Google Cloud Run**.

**Hackathon track: The Taskmaster** — Kira takes a single user intent, plans a multi-step video production pipeline, and completes the entire workflow autonomously with a human approval gate between planning and execution.

---

## Try it on WhatsApp

Scan the QR code to chat with Kira directly on WhatsApp:

<p align="center">
  <img src="qr.png" alt="Chat with Kira on WhatsApp" width="250" />
</p>

---

## Features

- WhatsApp (or web simulator) as the UI — no app to install
- Planning agent researches trending topics and drafts a creative brief
- Human-in-the-loop approval before any media is generated
- Execution agent runs image generation, video generation, voiceover, background music, caption burn-in, and YouTube upload in sequence
- Per-user persistent memory — Kira remembers standing instructions ("no temple content", "more engineering") and past topics across sessions
- Degrades gracefully — works without Postgres, GCS, or Twilio

---

## System Architecture

### Overall System

![Overall System Design](assets/overall_system_design.png)

### Agent Pipeline

![Agent System Diagram](assets/agent_system_diagram.png)

```
WhatsApp (Twilio) ──> FastAPI server ──> Google ADK (Gemini 3.5 Flash)
                                              │
                    ┌─────────────────────────┤
                    ▼                         ▼
              Planning Agent           Execution Agent
              (research, brief)        (image, video, TTS, music, mux, publish)
                                              │
                    ┌────────────┬─────────────┼──────────────┐
                    ▼            ▼             ▼              ▼
                 Fal.ai       FFmpeg     Google Cloud      YouTube
              (image/video/   (concat,    Storage         (optional
               TTS/music)    captions)   (public URLs)     upload)
```

**Storage layers:**
- **Postgres** (Supabase / Cloud SQL) — users, sessions, messages, productions, per-user memory (JSONB)
- **Google Cloud Storage** — generated images, per-shot video clips, final videos
- **YouTube** — optional final video upload for the channel owner

---

## Technologies Used

| Layer | Technology |
|---|---|
| LLM | Gemini 3.5 Flash (via Google AI API) |
| Agent framework | Google ADK (Agent Development Kit) |
| Compute | Google Cloud Run |
| Object storage | Google Cloud Storage |
| Database | Supabase (Postgres) / Cloud SQL |
| Media generation | Fal.ai (Flux image, video, TTS, Lyria music) |
| Media processing | FFmpeg |
| Messaging | Twilio WhatsApp API |
| Web server | FastAPI + Uvicorn |

---

## How It Works

1. You send a WhatsApp message (or use the web simulator)
2. Kira's planning agent researches trends, picks a topic, and drafts a creative brief
3. You approve (or steer) the brief
4. The execution agent runs a multi-step pipeline: image generation, video generation, voiceover, music, caption burn-in, and publishing
5. You get back a link to the finished YouTube Short (or a public GCS URL)

Messages within a 2-hour window belong to the same session. After 2 hours of silence, the next message starts a fresh session.

---

## Setup

### Prerequisites

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/download.html) installed and on PATH
- [ngrok](https://ngrok.com/download) — for WhatsApp webhook (or deploy to Cloud Run)
- API keys:
  - **Google AI** (Gemini) — [get one here](https://aistudio.google.com/apikey)
  - **fal.ai** — [get one here](https://fal.ai/dashboard/keys)

### Install

```bash
git clone https://github.com/badhrisuresh/Kira.git
cd Kira
python -m venv venv
```

Activate the virtual environment:

```bash
# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

### Environment Variables

Create a file at `kira/.env`:

```env
# Required
GOOGLE_API_KEY=<your-gemini-api-key>
FAL_KEY=<your-fal-ai-key>

# Optional — Postgres persistence (Supabase or any Postgres)
DATABASE_URL=postgresql://user:pass@host:6543/postgres

# Optional — Google Cloud Storage for media
GCS_BUCKET_NAME=your-bucket-name

# Optional — Twilio WhatsApp
TWILIO_ACCOUNT_SID=<sid>
TWILIO_AUTH_TOKEN=<token>
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
```

All optional variables degrade gracefully — Kira works without Postgres (in-memory sessions, file-based memory), without GCS (uses ephemeral Fal.ai URLs), and without Twilio (web simulator only).

### Database (optional)

Kira uses Postgres to persist users, sessions, messages, productions, and per-user memory. The schema is auto-created on startup.

**Quick setup with Supabase (free tier):**
1. Create a project at [supabase.com](https://supabase.com)
2. Go to Settings > Database > Connection string > **Transaction pooler** (port 6543)
3. Copy the connection string into `DATABASE_URL` in your `.env`

When `DATABASE_URL` is unset, everything still works — sessions live in memory and memory uses a local JSON file.

### Google Cloud Storage (optional)

GCS stores generated images and videos with permanent public URLs (Fal.ai URLs expire after a few hours).

```bash
# Create bucket
gcloud storage buckets create gs://your-bucket-name --project=your-project

# Make objects publicly readable
gcloud storage buckets add-iam-policy-binding gs://your-bucket-name \
  --member=allUsers --role=roles/storage.objectViewer
```

Set `GCS_BUCKET_NAME=your-bucket-name` in your `.env`.

### YouTube OAuth (optional)

Only needed if you want Kira to upload directly to YouTube. Without this, videos are published to GCS with a public shareable link.

1. Create OAuth 2.0 credentials in [Google Cloud Console](https://console.cloud.google.com/apis/credentials) with the YouTube Data API v3 enabled
2. Download the client secret JSON
3. Run the OAuth flow once to generate `token.pickle`:
   ```bash
   python setup_oauth.py
   ```

### Run Locally

```bash
uvicorn kira.server:app --reload --port 8080
```

Open `http://localhost:8080` for the web simulator.

### WhatsApp Integration (optional)

1. Set up a [Twilio WhatsApp sandbox](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn)
2. Add `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` to your `.env`
3. Run [ngrok](https://ngrok.com) to expose your local server:
   ```bash
   ngrok http 8080
   ```
4. In Twilio's sandbox settings, set the webhook URL to:
   ```
   https://<your-ngrok-url>/whatsapp
   ```

### Docker

```bash
docker build -t kira .
docker run -p 8080:8080 --env-file kira/.env kira
```

### Deploy to Google Cloud Run

```bash
gcloud run deploy kira \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_API_KEY=<key>,FAL_KEY=<key>,GCS_BUCKET_NAME=<bucket>
```

Set remaining secrets (DATABASE_URL, Twilio) via `--set-env-vars` or Cloud Run's Secret Manager integration.

---

## Reproducible Testing

Kira relies on external AI services (Gemini, Fal.ai) and media tools (FFmpeg), so end-to-end runs are non-deterministic. Use the steps below to verify each layer independently.

### Sample assets

The `assets/` folder contains pre-generated sample files you can use to test individual pipeline stages without calling external APIs:

| File | Purpose |
|------|---------|
| `01_star_core_lightning.png/mp4` | Sample image + video for shot 1 |
| `02_black_hole_reveal.png/mp4` | Sample image + video for shot 2 |
| `03_accretion_disk_hero.png/mp4` | Sample image + video for shot 3 |
| `narration_voiceover.mp3` | Pre-recorded voiceover |
| `background_music.mp3` | Generated background track |
| `narration_script.txt` | Script used to generate the voiceover |
| `kira_agent_architecture_diagram.png` | Agent architecture diagram |
| `overall_system_design.png` | System design overview |
| `whatsapp_demo_*.png` | WhatsApp interaction screenshots |
| `youtube_studio_*.png` | YouTube Studio dashboard screenshots |

Use these to test FFmpeg concatenation, muxing, and caption burn-in without waiting for AI generation.

### 1. Server health check

Start the server and confirm the web simulator loads:

```bash
uvicorn kira.server:app --reload --port 8080
```

Open `http://localhost:8080` — you should see the chat UI. Send a simple message like "hi" and verify Kira responds without errors.

### 2. Tool-level smoke tests

Test individual tools in isolation by running them from a Python shell with your `.env` loaded:

```bash
python -c "
from dotenv import load_dotenv; load_dotenv('kira/.env')
from kira.tools.image_gen import generate_image
result = generate_image(prompt='a sunset over mountains', aspect_ratio='9:16')
print(result)
"
```

Repeat for other tools (`generate_video`, `generate_voiceover`, `generate_background_music`) to confirm API keys are valid and each service returns a URL.

### 3. FFmpeg pipeline

Verify FFmpeg is installed and the media pipeline works:

```bash
ffmpeg -version
```

To test concatenation and muxing without hitting AI APIs, use the sample clips in `assets/`:

```bash
python -c "
from kira.tools.concat_videos import concat_videos
result = concat_videos(video_urls=['assets/01_star_core_lightning.mp4', 'assets/02_black_hole_reveal.mp4', 'assets/03_accretion_disk_hero.mp4'])
print(result)
"
```

### 4. Database connectivity (optional)

If using Postgres, verify the connection:

```bash
python -c "
from dotenv import load_dotenv; load_dotenv('kira/.env')
from kira.db import get_pool
import asyncio
async def check():
    pool = await get_pool()
    async with pool.acquire() as conn:
        print(await conn.fetchval('SELECT 1'))
asyncio.run(check())
"
```

### 5. YouTube upload (optional)

Use the included test script to verify OAuth credentials:

```bash
python test_upload.py
```

This uploads `test.mp4` as a **private** video. Requires `client_secret.json` and a valid `token.pickle` (run `python setup_oauth.py` first if needed).

### 6. WhatsApp webhook (optional)

With ngrok running (`ngrok http 8080`), send a test message from the Twilio sandbox. Check server logs for the incoming webhook and Kira's response.

### Tips for reproducibility

- **Pin your `.env`**: keep a `.env.example` (without real keys) so collaborators know which variables to set.
- **Seed your prompts**: when debugging, send the same message to Kira repeatedly — the planning agent may pick different topics, but the pipeline path stays consistent.
- **Check logs**: the server logs every tool call and API response at `INFO` level. Run with `--log-level debug` for full request/response bodies.

---

## Findings & Learnings

- **ADK's session model** maps cleanly onto WhatsApp's 24-hour messaging window — each conversation is a session, and the 2-hour inactivity cutoff mirrors natural chat behavior.
- **Async pipeline coordination** between the planning agent and execution agent requires careful state handoff; we thread a `production_id` through both agents so progress can be resumed after disconnects.
- **Fal.ai queue-based generation** (image, video, TTS, music) means each step is independently retryable without restarting the whole pipeline.
- **FFmpeg for caption burn-in** proved more reliable than cloud-based subtitle services for short-form vertical video at 9:16 aspect ratio.
- **Gemini 3.5 Flash** struck the right balance between reasoning quality and latency for the planning agent — the full brief generation (research + script + shot list) completes in under 10 seconds.
