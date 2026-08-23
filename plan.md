# Kira — Final Implementation Plan (Two-Agent Architecture)
## For handoff to a coding agent

---

## Overview

Kira is an autonomous YouTube Shorts agent built on Google ADK.
She has two agents working as a team:

1. **Research Agent (Kira)** — the strategist. She checks trends,
   reads memory, proposes topics, converses with the user, takes
   steering, and produces a confirmed creative brief. This is the
   agent the user talks to in the chat UI.

2. **Execution Agent** — the producer. Once the research agent
   confirms a creative brief, she hands off to the execution agent
   which runs autonomously: generates reference images, produces
   video with audio, uploads to YouTube, and updates memory.
   No conversation, no approval gates, just execution.

The research agent is the `root_agent` (required by ADK). The
execution agent is a sub-agent that receives work via session state.

---

## Stack (exact model strings and endpoints)

| Component        | Provider / Model                                | Notes |
|-----------------|------------------------------------------------|-------|
| Agent framework  | Google ADK (`pip install google-adk`)           | Two `LlmAgent`s in parent/sub hierarchy |
| LLM backbone     | `gemini-3.5-flash`                              | Both agents use this model |
| Image gen        | Nano Banana Pro via Gemini API                  | Model: `gemini-3-pro-image-preview`, SDK: `google-genai` |
| Video gen        | Veo 3.1 reference-to-video via fal.ai          | Endpoint: `fal-ai/veo3.1/reference-to-video` |
| YouTube upload   | YouTube Data API v3                             | OAuth already configured, `token.pickle` exists |
| Frontend         | `adk web` (built-in ADK chat UI)                | No custom frontend needed |
| Memory           | Local `memory.json` file                        | Read/written by tools |
| Trends           | Google Trends via pytrends                      | For autonomous topic discovery |

---

## Project structure

```
kira/
├── __init__.py              # empty, required by ADK
├── agent.py                 # root_agent (research) + execution sub-agent
├── tools/
│   ├── __init__.py
│   ├── memory.py            # read_memory(), write_memory()
│   ├── trends.py            # search_trends()
│   ├── image_gen.py         # generate_image() → Nano Banana Pro
│   ├── video_gen.py         # generate_video() → Veo 3.1 ref-to-video
│   └── youtube.py           # upload_to_youtube()
├── memory.json              # persistent topic history + steering
├── token.pickle             # YouTube OAuth refresh token (exists)
├── client_secret.json       # YouTube OAuth client (exists)
└── .env                     # API keys
```

---

## .env

```
GOOGLE_API_KEY=<gemini-api-key-from-aistudio.google.com>
FAL_KEY=<fal-ai-api-key-from-fal.ai/dashboard>
```

---

## agent.py — two-agent architecture

```python
from google.adk.agents import LlmAgent
from tools.memory import read_memory, write_memory
from tools.trends import search_trends
from tools.image_gen import generate_image
from tools.video_gen import generate_video
from tools.youtube import upload_to_youtube

MODEL = "gemini-3.5-flash"

# ──────────────────────────────────────────────
# AGENT 2: Execution Agent (sub-agent)
# ──────────────────────────────────────────────
# Receives a confirmed creative brief via session state.
# Runs autonomously — no conversation, no approval gates.
# Generates images, video, uploads, updates memory.

execution_agent = LlmAgent(
    name="execution_agent",
    model=MODEL,
    description=(
        "Production agent that takes a confirmed creative brief and "
        "autonomously produces the final video: generates reference "
        "images, generates an 8-second video with native audio, "
        "uploads to YouTube, and saves the result to memory. "
        "Transfer to this agent ONLY after the user has confirmed "
        "the topic and creative brief."
    ),
    instruction="""You are Kira's production team. You receive a confirmed
creative brief and execute it without asking questions.

The creative brief is in the conversation history from the research agent.
Extract from it:
- The topic and hook fact
- The visual scene description
- The source citation
- The YouTube title and description

Then execute these steps in order:

STEP 1 — GENERATE REFERENCE IMAGES
Call generate_image() one or more times to create reference images
for the video scene. Use detailed prompts with:
- 9:16 vertical aspect ratio
- Cinematic photorealistic style
- Indian mythological/historical epic visual style
- Dramatic lighting, atmospheric haze
- Characters described by appearance, NOT by mythological name
- No text overlays in the image
Each call returns a URL. Collect all URLs.

STEP 2 — GENERATE VIDEO
Call generate_video() with:
- image_urls: the list of reference image URLs from step 1
- prompt: a motion and audio prompt describing camera movement,
  character action, and desired sound/music
This produces a single 8-second video with native audio.
Returns a video URL.

STEP 3 — UPLOAD TO YOUTUBE
Call upload_to_youtube() with:
- video_url: the URL from step 2
- title: from the creative brief (include #Shorts)
- description: from the creative brief (include source citation)
Returns a YouTube video ID.

STEP 4 — UPDATE MEMORY
Call write_memory() with:
- topic: the topic from the creative brief
- video_id: the YouTube video ID from step 3
- clear_next: True (if a one-time instruction was used)

STEP 5 — REPORT
Tell the user exactly what you produced:
- The topic and why
- The YouTube video ID
- A one-line summary of what the video shows

Do NOT ask questions. Do NOT wait for confirmation. Execute all
steps and report when done.""",
    tools=[
        generate_image,
        generate_video,
        upload_to_youtube,
        write_memory,
    ],
)

# ──────────────────────────────────────────────
# AGENT 1: Research Agent (root_agent)
# ──────────────────────────────────────────────
# This is the agent the user talks to in adk web.
# It converses, reasons, proposes topics, takes feedback,
# and delegates to execution_agent when the brief is confirmed.

root_agent = LlmAgent(
    name="kira",
    model=MODEL,
    description="Kira — autonomous content strategist for a YouTube Shorts channel.",
    instruction="""You are Kira, an autonomous content strategist for a
YouTube Shorts channel about Indian history, mythology, civilization,
and engineering marvels.

## Your job: research, propose, get confirmation, then hand off.

### STEP 1 — RESEARCH
When the user asks you to create a post (or when triggered):
- Call search_trends() to see what's trending today in India.
- Call read_memory() to see past topics and user steering.

### STEP 2 — PROPOSE
Based on trends and memory, propose a topic. Pick something that is:
- Trending or adjacent to something trending right now
- Anchored to a real, documented, citable fact
- Visually spectacular (temples, battles, scale, nature, engineering)
- NOT a repeat of any topic in your memory
- Compliant with standing instructions from the user
- Following any one-time "next" instruction if present

Present your proposal to the user:
- What topic you picked
- Why (what's trending, what gap you found)
- A brief description of the visual scene
- The source/citation for the fact

### STEP 3 — CONVERSE
The user may:
- Confirm ("sounds good", "go ahead", "yes") → proceed to step 4
- Redirect ("do something else", "what about X") → propose again
- Steer ("next time do Y", "no more Z") → save steering, then
  either continue with current proposal or propose a new one

If the user gives steering instructions:
- "next time do X" → call write_memory(next_instruction="X")
- "always do X" / "no more Y" → call write_memory(standing_instruction="...")
- Confirm what you saved.

### STEP 4 — HAND OFF
Once the user confirms, compose a clear creative brief containing:
- Topic and hook fact
- Detailed visual scene description for image generation
- Motion and audio description for video generation
- YouTube title (under 60 chars, include #Shorts)
- YouTube description with source citation
- Source/citation

Then transfer to execution_agent. Say something like:
"Got it. Handing off to production — I'll let you know when it's live."

The execution_agent will autonomously generate images, produce the
video, upload to YouTube, and update memory. It will report back
when done.

## Personality
You are a colleague, not a tool. You have opinions about what will
perform well. You explain your reasoning. You push back if the user
suggests something you think won't work, but you defer to them if
they insist. You speak concisely and with confidence.
""",
    tools=[
        search_trends,
        read_memory,
        write_memory,
    ],
    sub_agents=[execution_agent],
)
```

---

## Tool implementations

### tools/memory.py

```python
import json
import os
from datetime import date

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "memory.json")

def read_memory() -> dict:
    """Read Kira's memory of all past topics posted, user steering
    instructions (both standing and one-time), and any performance notes.
    Returns a dict with keys: topics (list of past posts with topic,
    video_id, date), standing (list of permanent rules), next (one-time
    topic request or None)."""
    if not os.path.exists(MEMORY_PATH):
        return {"topics": [], "standing": [], "next": None}
    with open(MEMORY_PATH) as f:
        return json.load(f)

def write_memory(
    topic: str = "",
    video_id: str = "",
    standing_instruction: str = "",
    next_instruction: str = "",
    clear_next: bool = False,
) -> str:
    """Write to Kira's memory.
    - topic + video_id: call after posting a video to log it.
    - standing_instruction: a permanent rule for all future videos
      (e.g. 'no temple content', 'more engineering').
    - next_instruction: a one-time topic request for the next video only.
    - clear_next: set True after using a one-time 'next' instruction,
      so it doesn't repeat."""
    memory = read_memory()

    if topic:
        memory["topics"].append({
            "topic": topic,
            "video_id": video_id,
            "date": date.today().isoformat(),
        })

    if standing_instruction:
        memory["standing"].append(standing_instruction)

    if next_instruction:
        memory["next"] = next_instruction

    if clear_next:
        memory["next"] = None

    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)

    return "Memory updated successfully."
```

### tools/trends.py

```python
def search_trends() -> str:
    """Search Google Trends for what's trending today in India.
    Returns a list of today's top trending search topics which Kira
    can cross-reference with her channel's niche (Indian history,
    mythology, civilization, engineering, festivals)."""
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl='en-IN', tz=330)
        trending = pytrends.trending_searches(pn='india')
        topics = trending[0].tolist()[:20]
        return "Today's trending topics in India:\n" + "\n".join(
            f"- {t}" for t in topics
        )
    except Exception as e:
        return (
            f"Could not fetch trends ({e}). "
            "Pick a topic from your niche based on memory and "
            "general knowledge of upcoming Indian festivals or events."
        )
```

### tools/image_gen.py

```python
import os
import uuid
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

def generate_image(prompt: str) -> str:
    """Generate a reference image from a detailed text prompt using
    Nano Banana Pro (Gemini 3 Pro Image). Always include '9:16 vertical'
    in the prompt for YouTube Shorts format. Returns a URL that can be
    passed to generate_video()."""

    response = client.models.generate_content(
        model="gemini-3-pro-image-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio="9:16",
            ),
        ),
    )

    for part in response.candidates[0].content.parts:
        if hasattr(part, "inline_data") and part.inline_data:
            image_bytes = part.inline_data.data
            filename = f"/tmp/kira_ref_{uuid.uuid4().hex[:8]}.png"
            with open(filename, "wb") as f:
                f.write(image_bytes)

            # Upload to fal.ai storage so Veo 3.1 can access it
            import fal_client
            url = fal_client.upload_file(filename)
            return url

    return "ERROR: No image was generated. Try a different prompt."
```

### tools/video_gen.py

```python
import fal_client

def generate_video(
    image_urls: list[str],
    prompt: str,
) -> str:
    """Generate an 8-second video with native audio from 1-3 reference
    images using Veo 3.1 on fal.ai.

    Args:
        image_urls: List of 1-3 reference image URLs (from generate_image).
        prompt: Motion and audio prompt. Describe:
            - Camera movement (slow push-in, orbit, tracking shot)
            - Character/scene action
            - Desired audio mood (dramatic drums, serene, epic orchestral)
            - Always specify 9:16 vertical.

    Returns: URL of the generated 8-second video with audio."""

    result = fal_client.subscribe(
        "fal-ai/veo3.1/reference-to-video",
        arguments={
            "image_urls": image_urls,
            "prompt": prompt,
            "duration": "8",
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "generate_audio": True,
        },
        with_logs=True,
        on_queue_update=lambda update: None,
    )

    return result["video"]["url"]
```

### tools/youtube.py

```python
import os
import pickle
import urllib.request
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "token.pickle")

def upload_to_youtube(
    video_url: str,
    title: str,
    description: str,
) -> str:
    """Download a video from URL and upload it to YouTube as a private
    Short. Title should include #Shorts for YouTube classification.

    Args:
        video_url: URL of the video file (from generate_video).
        title: YouTube video title, under 60 characters, include #Shorts.
        description: YouTube description with source citation.

    Returns: YouTube video ID string."""

    # Download video from fal.ai
    local_path = "/tmp/kira_upload.mp4"
    urllib.request.urlretrieve(video_url, local_path)

    # Load and refresh OAuth credentials
    with open(TOKEN_FILE, "rb") as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    # Upload
    youtube = build("youtube", "v3", credentials=creds)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22",
            },
            "status": {"privacyStatus": "private"},
        },
        media_body=MediaFileUpload(local_path, resumable=True),
    )
    response = request.execute()
    return response["id"]
```

---

## How it runs

```bash
cd kira/
adk web
```

Opens http://localhost:8000. The chat UI talks to `kira` (root_agent).

### Typical flow:

```
You:    "What should we post today?"

Kira:   [calls search_trends() → reads trends]
        [calls read_memory() → reads past topics]
        "Ram Navami is trending — search volume up 300%.
         I've already done the Shiva Dhanush scene. Nobody's
         covered the Agni Pariksha. I'd do that.
         Here's the brief: ..."

You:    "Sounds good, go ahead"

Kira:   "Got it. Handing off to production."
        [transfers to execution_agent]

        execution_agent:
        [calls generate_image() × 2 → reference images]
        [calls generate_video() → 8s video with audio]
        [calls upload_to_youtube() → uploads as private]
        [calls write_memory() → logs the post]

Kira:   "Done. Posted 'The Trial by Fire — Agni Pariksha #Shorts'
         Video ID: abc123. It's private — flip to public when ready."
```

### Steering:

```
You:    "No more mythology for a while, focus on engineering"

Kira:   [calls write_memory(standing_instruction="no mythology, focus on engineering")]
        "Noted. Engineering focus from now on."
```

---

## Pricing per video

| Component | Cost |
|-----------|------|
| Gemini 3.5 Flash (both agents' reasoning) | ~$0.02 |
| Nano Banana Pro (1-3 reference images) | ~$0.04-0.12 |
| Veo 3.1 ref-to-video (8s, audio on, 720p standard) | ~$3.20 |
| Veo 3.1 ref-to-video (8s, audio on, 720p fast) | ~$1.20 |
| YouTube upload | Free |
| **Total per video** | **~$1.40 (fast) to ~$3.35 (standard)** |

For hackathon (10 test videos): ~$14-34 total.

---

## Build order (2 weekends)

### Weekend 1, Saturday — the brain
1. `pip install google-adk google-genai fal-client google-api-python-client google-auth-oauthlib google-auth-httplib2 pytrends Pillow`
2. Create project structure, `.env`, `__init__.py`
3. Implement `tools/memory.py` — test read/write manually
4. Implement `tools/trends.py` — test pytrends call
5. Create `agent.py` with ONLY the research agent (root_agent)
   with tools: `search_trends`, `read_memory`, `write_memory`
   and NO sub_agents yet
6. Run `adk web`, test the conversational loop:
   - "What should we post today?" → trends + memory + proposal
   - "Do something else" → new proposal
   - "Next time do Karna" → writes to memory
7. This proves the strategist brain works before touching any API

### Weekend 1, Sunday — the hands
8. Implement `tools/image_gen.py` — test standalone image generation
9. Implement `tools/video_gen.py` — test standalone video generation
10. Create the execution_agent with tools: `generate_image`,
    `generate_video`, `upload_to_youtube`, `write_memory`
11. Add `sub_agents=[execution_agent]` to root_agent
12. Test the full handoff flow:
    - Research agent proposes → user confirms → execution runs
    - Watch: does the brief transfer cleanly?
    - Watch: does execution_agent call tools in the right order?

### Weekend 2, Saturday — end to end
13. Wire `tools/youtube.py` — test upload (token.pickle exists)
14. Full pipeline: trigger → research → confirm → execute → YouTube
15. Run 3-5 times, build real history in memory.json
16. Verify: Kira avoids repeating topics (memory works)
17. Verify: Kira references real trending data (trends work)
18. Verify: steering persists across runs (standing instructions work)
19. Verify: one-time instructions clear after use (next works)

### Weekend 2, Sunday — demo and submit
20. Polish image/video prompts for consistent visual style
21. Ensure all titles include #Shorts
22. Record demo video (3 minutes):
    - Open `adk web`, type "what's trending? create a post"
    - Show Kira reading trends, reading memory, proposing topic
    - Show the user confirming
    - Show handoff to execution ("handing off to production")
    - Show video generation completing
    - Show YouTube Studio with the uploaded video
    - Type "less mythology, more engineering"
    - Run again — show Kira picking an engineering topic
    - Tagline: "Kira works nights."
23. Write Devpost submission
24. Buffer for whatever broke Saturday

---

## Key dependencies

```bash
pip install google-adk google-genai fal-client \
    google-api-python-client google-auth-oauthlib \
    google-auth-httplib2 pytrends Pillow
```

---

## Notes for the coding agent

### ADK specifics
- The ONLY required export in `agent.py` is `root_agent`.
  ADK discovers it automatically via the name.
- `execution_agent` must be defined BEFORE `root_agent` in the file,
  because Python needs it to exist before it's referenced in
  `sub_agents=[execution_agent]`.
- ADK handles delegation natively: when root_agent's LLM decides
  to transfer to execution_agent (based on its description matching
  the task), control passes to the sub-agent. The sub-agent runs
  until done, then control returns to root_agent.
- The `description` field on execution_agent is critical — this is
  what Gemini reads to decide WHEN to delegate. Make it clear and
  specific about what triggers the handoff.
- `output_key` on execution_agent is optional but useful for
  capturing the result back in session state.
- `adk web` serves the chat UI on localhost:8000. No Flask, no
  React, no HTML to build.

### Tool specifics
- All tools are plain Python functions with type hints and
  complete docstrings. ADK reads docstrings to tell Gemini what
  each tool does, so they must be clear and accurate.
- Image gen returns a fal.ai hosted URL (uploaded via
  `fal_client.upload_file()`). Video gen consumes those URLs.
- Video gen returns a fal.ai hosted URL. YouTube upload downloads
  from that URL and re-uploads.
- The Veo 3.1 reference-to-video endpoint accepts `image_urls`
  as a LIST of URLs (1-3 images). Multiple reference images help
  with character consistency across the scene.
- `generate_image()` may be called multiple times by execution_agent.
  All returned URLs are collected and passed as a list to
  `generate_video()`.

### Files that already exist (do NOT regenerate)
- `token.pickle` — YouTube OAuth refresh token
- `client_secret.json` — YouTube OAuth client credentials

### MVP scope
- Single 8-second video per run (no multi-shot stitching)
- Private YouTube uploads (flip to public manually)
- No TTS — Veo 3.1 handles audio natively
- No ffmpeg assembly — single video clip, no stitching needed
