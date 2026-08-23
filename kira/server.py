"""FastAPI server wrapping Kira's ADK pipeline behind a REST + SSE API.

Run locally:  uvicorn kira.server:app --reload --port 8080
Or:           python -m kira.server
"""

import asyncio
import json
import logging
import os
import re
import traceback
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Load .env from the kira package directory (same as ADK does)
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    load_dotenv(_env_path)
    logging.info(f"Loaded .env from {_env_path}")

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from .agent import root_agent
from .events import event_bus, ProductionEvent
from .tools.memory import read_memory, write_memory

logger = logging.getLogger(__name__)

# ── ADK runner setup ──────────────────────────────────────────────

APP_NAME = "kira"
session_service = InMemorySessionService()
runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)

# Track active session and production state
_state = {
    "session_id": None,
    "user_id": "kira-web-user",
    "status": "idle",            # idle | proposing | proposed | producing | done | error
    "current_proposal": None,    # Parsed proposal dict
    "production_result": None,   # Final result after production
    "error": None,
}

# ── Helpers ───────────────────────────────────────────────────────

async def _get_or_create_session():
    """Get existing session or create a new one."""
    if _state["session_id"]:
        session = await session_service.get_session(
            app_name=APP_NAME,
            user_id=_state["user_id"],
            session_id=_state["session_id"],
        )
        if session:
            return session
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=_state["user_id"],
    )
    _state["session_id"] = session.id
    return session


async def _send_message(text: str) -> str:
    """Send a message to the ADK agent and collect the full response."""
    session = await _get_or_create_session()
    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=text)],
    )
    response_parts = []
    async for event in runner.run_async(
        user_id=_state["user_id"],
        session_id=session.id,
        new_message=content,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    response_parts.append(part.text)
    return "\n".join(response_parts)


def _clean_markdown(text: str) -> str:
    """Strip markdown formatting from text."""
    text = re.sub(r'^#{1,4}\s*', '', text)       # heading markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # bold
    text = re.sub(r'\*([^*]+)\*', r'\1', text)     # italic
    text = re.sub(r'^\s*[\*\-]\s*', '', text)      # list bullets
    return text.strip()


def _parse_proposal(raw_text: str) -> dict:
    """Parse the agent's proposal text into structured data.

    Kira's research agent typically responds with:
    1. A dump of trending stories/web results
    2. Then a proposal section starting with "I propose..." or "### Proposal"

    We need to find the actual proposal, not the preamble.
    """
    topic = ""
    why = ""
    visual = ""
    source = ""
    trending = ""

    # Strategy 1: Look for "I propose we cover: **Topic Name**" pattern
    propose_match = re.search(
        r'I propose (?:we cover|covering|this topic)[:\s]*\*\*([^*]+)\*\*',
        raw_text, re.IGNORECASE
    )
    if propose_match:
        topic = propose_match.group(1).strip().rstrip('.')

    # Strategy 2: Look for "### Proposal: Topic" pattern
    if not topic:
        heading_match = re.search(
            r'#{1,4}\s*(?:Proposal|Topic|My Proposal)[:\s—\-]*(.+)',
            raw_text, re.IGNORECASE
        )
        if heading_match:
            topic = _clean_markdown(heading_match.group(1))

    # Strategy 3: Look for "**Topic:**" or "**The Topic:**" pattern
    if not topic:
        bold_match = re.search(
            r'\*\*(?:The )?Topic[:\s]*\*\*[:\s]*(.+)',
            raw_text, re.IGNORECASE
        )
        if bold_match:
            topic = _clean_markdown(bold_match.group(1))

    # Parse sections from the proposal part of the text
    # Find where the actual proposal starts
    proposal_start = 0
    for marker in [r'I propose', r'###\s*Proposal', r'###\s*Why This Topic',
                   r'My proposal', r'I recommend']:
        m = re.search(marker, raw_text, re.IGNORECASE)
        if m:
            proposal_start = m.start()
            break

    proposal_text = raw_text[proposal_start:]
    lines = proposal_text.split("\n")

    current_section = None
    for line in lines:
        line_lower = line.lower().strip()
        if not line.strip():
            continue

        if any(kw in line_lower for kw in ["why this topic", "why it will perform",
                                            "why:", "the trend hook"]):
            current_section = "why"
            extracted = re.sub(r'(?i).*?:\s*', '', line, count=1).strip()
            if extracted and not why:
                why = _clean_markdown(extracted)
        elif any(kw in line_lower for kw in ["visual", "the visuals", "visual angle",
                                              "visual concept"]):
            current_section = "visual"
            extracted = re.sub(r'(?i).*?:\s*', '', line, count=1).strip()
            if extracted:
                visual = _clean_markdown(extracted)
        elif any(kw in line_lower for kw in ["source", "citation", "the source"]):
            current_section = "source"
            extracted = re.sub(r'(?i).*?:\s*', '', line, count=1).strip()
            if extracted:
                source = _clean_markdown(extracted)
        elif any(kw in line_lower for kw in ["hook:", "the hook", "core hook",
                                              "the fact", "the angle"]):
            current_section = "hook"
            if not why:
                extracted = re.sub(r'(?i).*?:\s*', '', line, count=1).strip()
                if extracted:
                    why = _clean_markdown(extracted)
        elif current_section == "why" and not why:
            cleaned = _clean_markdown(line)
            if len(cleaned) > 20:
                why = cleaned
        elif current_section == "hook" and not why:
            cleaned = _clean_markdown(line)
            if len(cleaned) > 20:
                why = cleaned

    # Fallback topic: first bold text in proposal area
    if not topic:
        bold_match = re.search(r'\*\*([^*]{8,80})\*\*', proposal_text)
        if bold_match:
            topic = bold_match.group(1).strip().rstrip('.')

    # Fallback topic: first heading in proposal area
    if not topic:
        for line in lines:
            cleaned = _clean_markdown(line)
            if cleaned and 8 < len(cleaned) < 100:
                topic = cleaned
                break

    # Fallback why
    if not why:
        for line in lines:
            cleaned = _clean_markdown(line)
            if len(cleaned) > 40 and cleaned != topic:
                why = cleaned
                break

    # Extract trending percentage from raw text
    pct_match = re.search(r'(?:up|↑|rising)\s*(?:a staggering\s*)?(\d[\d,.]*\s*%)', raw_text, re.IGNORECASE)
    if pct_match:
        trending = f"+{pct_match.group(1).strip()}"
    elif not trending:
        pct_match = re.search(r'(\d[\d,.]*%)', raw_text)
        if pct_match:
            trending = f"+{pct_match.group(1)}"

    # Final cleanup on all fields
    topic = re.sub(r'^(?:Topic|Proposal)\s*[:—\-]\s*', '', topic, flags=re.IGNORECASE).strip()
    why = re.sub(r'^\*\s*', '', why).strip()
    source = re.sub(r'^\*\s*', '', source).strip()
    visual = re.sub(r'^\*\s*', '', visual).strip()

    return {
        "topic": topic or "Untitled Proposal",
        "why": why or "Kira found this topic worth exploring.",
        "visual": visual,
        "source": source,
        "trending": trending or "Trending",
        "raw": raw_text,
    }


# ── Lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Kira server starting")
    yield
    logger.info("Kira server shutting down")


# ── FastAPI app ───────────────────────────────────────────────────

app = FastAPI(title="Kira", lifespan=lifespan)

# Serve static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Routes ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html")) as f:
        return HTMLResponse(f.read())


@app.get("/api/status")
async def get_status():
    return {
        "status": _state["status"],
        "proposals": _state["current_proposal"],  # list of proposals or None
        "result": _state["production_result"],
        "error": _state["error"],
        "phases": [
            {"phase": e.phase, "status": e.status, "detail": e.detail,
             "progress": e.progress, "preview_url": e.preview_url,
             "error_message": e.error_message}
            for e in event_bus.history
        ],
    }


def _parse_multiple_proposals(raw_text: str) -> list[dict]:
    """Parse 3 proposals from agent response into a list of dicts."""
    # Split by option markers: "Option 1", "**1.**", "### 1.", etc.
    splits = re.split(
        r'(?:^|\n)\s*(?:#{1,4}\s*)?(?:\*\*)?(?:Option|Proposal|Topic)\s*(\d)[\s.:—\-\)]+',
        raw_text, flags=re.IGNORECASE
    )

    proposals = []

    if len(splits) >= 3:
        # We got splits — parse each chunk
        # splits[0] is preamble, then alternating (number, content)
        for i in range(1, len(splits), 2):
            if i + 1 < len(splits):
                chunk = splits[i + 1]
                p = _parse_proposal(chunk)
                if p["topic"] != "Untitled Proposal":
                    proposals.append(p)

    # Fallback: try splitting by numbered bold items "**1. Topic**" or "1. **Topic**"
    if len(proposals) < 2:
        proposals = []
        bold_topics = re.findall(
            r'(?:^|\n)\s*\d+[\.\)]\s*\*\*([^*]{5,80})\*\*',
            raw_text
        )
        if len(bold_topics) >= 2:
            for bt in bold_topics[:3]:
                # Find the section for each topic
                topic_clean = bt.strip().rstrip('.')
                idx = raw_text.find(bt)
                # Get text after this topic until next numbered item or end
                remaining = raw_text[idx + len(bt):]
                next_num = re.search(r'\n\s*\d+[\.\)]\s*\*\*', remaining)
                chunk = remaining[:next_num.start()] if next_num else remaining[:500]

                why = ""
                source = ""
                trending = ""

                for line in chunk.split("\n"):
                    cl = _clean_markdown(line)
                    ll = line.lower()
                    if any(kw in ll for kw in ["why", "hook", "angle", "perform"]):
                        why = _clean_markdown(re.sub(r'(?i).*?:\s*', '', line, count=1))
                    elif any(kw in ll for kw in ["source", "citation"]):
                        source = _clean_markdown(re.sub(r'(?i).*?:\s*', '', line, count=1))
                    elif not why and len(cl) > 30:
                        why = cl

                pct = re.search(r'(\d[\d,.]*%)', chunk)
                if pct:
                    trending = f"+{pct.group(1)}"

                proposals.append({
                    "topic": topic_clean,
                    "why": why or "A compelling space topic.",
                    "visual": "",
                    "source": source,
                    "trending": trending or "Trending",
                    "raw": chunk,
                })

    # Final fallback: parse as a single proposal and return it alone
    if len(proposals) < 1:
        single = _parse_proposal(raw_text)
        proposals = [single]

    return proposals[:3]


@app.get("/api/propose")
async def propose():
    """Ask Kira to research trends and propose 3 topics."""
    if _state["status"] == "producing":
        return JSONResponse(
            {"error": "Production in progress. Wait for it to finish."},
            status_code=409,
        )

    _state["status"] = "proposing"
    _state["current_proposal"] = None
    _state["production_result"] = None
    _state["error"] = None
    await event_bus.clear()

    try:
        response = await _send_message(
            "What should we post today? Research trends, check memory, "
            "and give me exactly 3 topic options to choose from. "
            "For each option, format as:\n"
            "1. **Topic Name** — one-line description of why this will work. "
            "(Source: citation)\n"
            "Keep each option to 2-3 lines max. I'll pick one."
        )
        proposals = _parse_multiple_proposals(response)
        _state["current_proposal"] = proposals
        _state["status"] = "proposed"
        return {"proposals": proposals}
    except Exception as e:
        logger.error(f"Propose failed: {e}\n{traceback.format_exc()}")
        _state["status"] = "error"
        _state["error"] = str(e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/skip")
async def skip():
    """Skip current proposals, ask for 3 new ones."""
    if _state["status"] not in ("proposed", "idle", "done", "error"):
        return JSONResponse(
            {"error": "Cannot skip right now."},
            status_code=409,
        )

    _state["status"] = "proposing"
    _state["current_proposal"] = None
    _state["error"] = None
    await event_bus.clear()

    try:
        response = await _send_message(
            "None of those grab me. Give me 3 completely different topic options. "
            "Same format: numbered, bold topic, one-line why, source."
        )
        proposals = _parse_multiple_proposals(response)
        _state["current_proposal"] = proposals
        _state["status"] = "proposed"
        return {"proposals": proposals}
    except Exception as e:
        logger.error(f"Skip failed: {e}\n{traceback.format_exc()}")
        _state["status"] = "error"
        _state["error"] = str(e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/approve")
async def approve(request: Request):
    """Approve a chosen topic and start async production."""
    if _state["status"] != "proposed" or not _state["current_proposal"]:
        return JSONResponse(
            {"error": "No proposal to approve."},
            status_code=409,
        )

    # Accept optional topic index from request body
    try:
        body = await request.json()
        chosen_idx = body.get("index", 0)
    except Exception:
        chosen_idx = 0

    # Store the chosen topic for the approval message
    proposals = _state["current_proposal"]
    if isinstance(proposals, list) and 0 <= chosen_idx < len(proposals):
        chosen = proposals[chosen_idx]
        _state["chosen_topic"] = chosen.get("topic", "")
    else:
        _state["chosen_topic"] = ""

    _state["status"] = "producing"
    _state["production_result"] = None
    _state["error"] = None
    await event_bus.clear()

    # Emit initial pending phases
    phases = [
        ("script", "Writing script"),
        ("plan", "Planning shots"),
        ("image_gen", "Generating images"),
        ("video_gen", "Generating video"),
        ("concat", "Assembling clips"),
        ("voiceover", "Recording voiceover"),
        ("music", "Creating music"),
        ("mux", "Mixing audio"),
        ("upload", "Uploading to YouTube"),
        ("memory", "Saving to memory"),
    ]
    for phase_id, phase_name in phases:
        await event_bus.emit(ProductionEvent(
            phase=phase_id,
            status="pending",
            detail=phase_name,
        ))

    # Start production in background
    asyncio.create_task(_run_production())
    return {"status": "producing", "message": "Production started."}


async def _run_production():
    """Run the full production pipeline via ADK, emitting events."""
    try:
        # Send approval to the agent — this triggers the full pipeline
        session = await _get_or_create_session()
        chosen = _state.get("chosen_topic", "")
        if chosen:
            approval_msg = f'I pick "{chosen}". Go ahead and make it.'
        else:
            approval_msg = "Looks great. Go ahead and make it."
        content = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=approval_msg)],
        )

        current_phase_idx = 0
        phase_order = ["script", "plan", "image_gen", "video_gen",
                       "concat", "voiceover", "music", "mux", "upload", "memory"]
        phase_labels = {
            "script": "Writing script",
            "plan": "Planning shots",
            "image_gen": "Generating images",
            "video_gen": "Generating video",
            "concat": "Assembling clips",
            "voiceover": "Recording voiceover",
            "music": "Creating music",
            "mux": "Mixing audio",
            "upload": "Uploading to YouTube",
            "memory": "Saving to memory",
        }

        # Map tool names to phases
        tool_to_phase = {
            "script_writer": "script",
            "production_planner": "plan",
            "generate_image": "image_gen",
            "generate_video": "video_gen",
            "concat_videos": "concat",
            "generate_voiceover": "voiceover",
            "generate_background_music": "music",
            "fit_and_mux_audio": "mux",
            "upload_to_youtube": "upload",
            "write_memory": "memory",
        }

        response_parts = []
        async for event in runner.run_async(
            user_id=_state["user_id"],
            session_id=session.id,
            new_message=content,
        ):
            # Track tool calls to update phases
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_parts.append(part.text)

                    # Detect function calls to map to production phases
                    if hasattr(part, 'function_call') and part.function_call:
                        tool_name = part.function_call.name
                        if tool_name in tool_to_phase:
                            phase = tool_to_phase[tool_name]
                            # Mark all prior phases as completed
                            phase_idx = phase_order.index(phase)
                            for i in range(current_phase_idx, phase_idx):
                                await event_bus.emit(ProductionEvent(
                                    phase=phase_order[i],
                                    status="completed",
                                    detail=f"{phase_labels[phase_order[i]]} — done",
                                    progress=1.0,
                                ))
                            # Mark current phase as in_progress
                            await event_bus.emit(ProductionEvent(
                                phase=phase,
                                status="in_progress",
                                detail=f"{phase_labels[phase]} ...",
                                progress=0.5,
                            ))
                            current_phase_idx = phase_idx

                    # Detect function responses (tool results)
                    if hasattr(part, 'function_response') and part.function_response:
                        resp_name = part.function_response.name
                        if resp_name in tool_to_phase:
                            phase = tool_to_phase[resp_name]
                            result_data = part.function_response.response
                            preview = None
                            # Extract preview URLs from image/video results
                            if isinstance(result_data, dict):
                                for v in result_data.values():
                                    if isinstance(v, str) and (
                                        v.startswith("http") and
                                        any(ext in v.lower() for ext in [".png", ".jpg", ".mp4", ".webm"])
                                    ):
                                        preview = v
                            elif isinstance(result_data, str):
                                if result_data.startswith("http"):
                                    preview = result_data

                            await event_bus.emit(ProductionEvent(
                                phase=phase,
                                status="completed",
                                detail=f"{phase_labels[phase]} — done",
                                progress=1.0,
                                preview_url=preview,
                            ))
                            current_phase_idx = phase_order.index(phase) + 1

        # Mark all remaining phases as completed
        for i in range(current_phase_idx, len(phase_order)):
            await event_bus.emit(ProductionEvent(
                phase=phase_order[i],
                status="completed",
                detail=f"{phase_labels[phase_order[i]]} — done",
                progress=1.0,
            ))

        full_response = "\n".join(response_parts)

        # Extract YouTube video ID from response
        video_id = None
        yt_match = re.search(r'(?:youtube\.com/shorts/|video[_ ]?(?:id|ID)[:\s]*)\s*([A-Za-z0-9_-]{11})', full_response)
        if yt_match:
            video_id = yt_match.group(1)

        _state["production_result"] = {
            "response": full_response,
            "video_id": video_id,
            "youtube_url": f"https://youtube.com/shorts/{video_id}" if video_id else None,
        }
        _state["status"] = "done"

        # Emit a final "done" sentinel
        await event_bus.emit(ProductionEvent(
            phase="done",
            status="completed",
            detail="Production complete!",
            progress=1.0,
            preview_url=f"https://youtube.com/shorts/{video_id}" if video_id else None,
        ))

    except Exception as e:
        logger.error(f"Production failed: {e}\n{traceback.format_exc()}")
        _state["status"] = "error"
        _state["error"] = str(e)
        await event_bus.emit(ProductionEvent(
            phase="error",
            status="error",
            detail="Production failed",
            error_message=str(e),
        ))


@app.get("/api/production/stream")
async def production_stream(request: Request):
    """SSE endpoint for live production progress."""
    queue = await event_bus.subscribe()

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield event.to_sse()
                    if event.phase == "done" or event.status == "error":
                        break
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        finally:
            await event_bus.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
async def get_history():
    memory = read_memory()
    return {
        "topics": list(reversed(memory.get("topics", []))),
        "total": len(memory.get("topics", [])),
    }


@app.get("/api/taste")
async def get_taste():
    memory = read_memory()
    return {
        "standing": memory.get("standing", []),
        "next": memory.get("next"),
    }


@app.post("/api/taste")
async def update_taste(request: Request):
    body = await request.json()
    instruction = body.get("instruction", "").strip()
    instruction_type = body.get("type", "standing")  # "standing" or "next"

    if not instruction:
        return JSONResponse({"error": "No instruction provided."}, status_code=400)

    if instruction_type == "next":
        write_memory(next_instruction=instruction)
    else:
        write_memory(standing_instruction=instruction)

    # Also tell the agent so it's aware in the current session
    try:
        await _send_message(
            f"User steering: {instruction}. "
            "Save this to memory and acknowledge."
        )
    except Exception:
        pass  # Memory is already saved directly

    return {"status": "ok", "instruction": instruction, "type": instruction_type}


@app.delete("/api/taste/{index}")
async def remove_taste(index: int):
    """Remove a standing instruction by index."""
    memory = read_memory()
    standing = memory.get("standing", [])
    if 0 <= index < len(standing):
        removed = standing.pop(index)
        memory["standing"] = standing
        import json
        from .tools.memory import MEMORY_PATH
        with open(MEMORY_PATH, "w") as f:
            json.dump(memory, f, indent=2)
        return {"status": "ok", "removed": removed}
    return JSONResponse({"error": "Invalid index."}, status_code=404)


# ── Entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
