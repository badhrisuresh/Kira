# Kira

Autonomous AI pipeline that researches trending topics, writes scripts, generates visuals, and publishes YouTube Shorts.

## Setup

### Prerequisites

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/download.html) installed and on PATH
- [ngrok](https://ngrok.com/download) — for WhatsApp integration and exposing your local server
- API keys:
  - **Google AI** (Gemini) — [get one here](https://aistudio.google.com/apikey)
  - **fal.ai** — [get one here](https://fal.ai/dashboard/keys)
  - **YouTube Data API v3** — needed for uploads, requires OAuth setup (see below)

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

```
GOOGLE_API_KEY=<your-gemini-api-key>
FAL_KEY=<your-fal-ai-key>
```

### YouTube OAuth (for uploads)

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

Open `http://localhost:8080` in your browser.

### WhatsApp Integration (optional)

1. Set up a [Twilio WhatsApp sandbox](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn)
2. Run [ngrok](https://ngrok.com) to expose your local server:
   ```bash
   ngrok http 8080
   ```
3. In Twilio's sandbox settings, set the webhook URL to:
   ```
   https://<your-ngrok-url>/whatsapp
   ```

### Docker (optional)

```bash
docker build -t kira .
docker run -p 8080:8080 --env-file kira/.env kira
```
