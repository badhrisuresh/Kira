# Kira

Autonomous AI video content generator that works via WhatsApp. Kira researches trending topics, writes scripts, generates AI images and video, produces voiceovers, composes background music, and publishes the final video — all from a single chat message.

Built with Google ADK (Gemini), Fal.ai, FFmpeg, and Twilio.

## Try it on WhatsApp

Scan the QR code to chat with Kira directly on WhatsApp:

<p align="center">
  <img src="qr.png" alt="Chat with Kira on WhatsApp" width="250" />
</p>

## How it works

1. You send a WhatsApp message (or use the web simulator)
2. Kira's planning agent researches trends, picks a topic, and drafts a creative brief
3. You approve (or steer) the brief
4. The execution agent runs a multi-step pipeline: image generation, video generation, voiceover, music, caption burn-in, and publishing
5. You get back a link to the finished YouTube Short (or a public GCS URL)

Each user gets their own memory — Kira remembers past topics, standing instructions ("no temple content", "more engineering"), and one-time requests.

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

### Run

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

Messages within a 2-hour window belong to the same session. After 2 hours of silence, the next message starts a fresh session.

### Docker (optional)

```bash
docker build -t kira .
docker run -p 8080:8080 --env-file kira/.env kira
```

## Architecture

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
- **Postgres** (Supabase) — users, sessions, messages, productions, per-user memory (JSONB)
- **Google Cloud Storage** — generated images, per-shot video clips, final videos
- **YouTube** — optional final video upload for the channel owner
