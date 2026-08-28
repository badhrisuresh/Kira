"""FastAPI server wrapping Kira's ADK pipeline behind a REST + SSE API.

Run locally:  uvicorn kira.server:app --reload --port 8080
Or:           python -m kira.server
"""

from __future__ import annotations

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

from .agent import build_agents
from . import block_manager
from .events import event_bus, ProductionEvent
from .tools.memory import read_memory, save_memory, write_memory

logger = logging.getLogger(__name__)

# ── ADK runner setup ──────────────────────────────────────────────

APP_NAME = "kira"
session_service = InMemorySessionService()

# Initialize from active block
_active_config = block_manager.get_active_block()
_active_block_id = _active_config["id"]
_active_block_path = block_manager.get_block_path(_active_block_id)
_root_agent = build_agents(_active_config, _active_block_path)
runner = Runner(
    agent=_root_agent,
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

# ── Tool-to-phase mapping (shared by /api/approve and /api/chat) ─

_TOOL_TO_PHASE = {
    "script_writer": "script",
    "production_planner": "plan",
    "generate_image": "image_gen",
    "generate_video": "video_gen",
    "concat_videos": "concat",
    "generate_voiceover": "voiceover",
    "generate_background_music": "music",
    "fit_and_mux_audio": "mux",
    "mux_music_only": "mux",
    "upload_to_youtube": "upload",
    "write_memory": "memory",
}
_PRODUCTION_TOOLS = set(_TOOL_TO_PHASE) - {"write_memory"}

# ── Helpers ───────────────────────────────────────────────────────


def _get_phases() -> list[tuple[str, str]]:
    """Build the production phase list from the active block config."""
    narration = _active_config.get("narration_enabled", True)
    phases = [
        ("script", "Writing script"),
        ("plan", "Planning shots"),
        ("image_gen", "Generating images"),
        ("video_gen", "Generating video"),
        ("concat", "Assembling clips"),
    ]
    if narration:
        phases.append(("voiceover", "Recording voiceover"))
    phases.append(("music", "Creating music"))
    phases.append(("mux", "Mixing audio" if narration else "Adding music"))
    phases.extend([
        ("upload", "Uploading to YouTube"),
        ("memory", "Saving to memory"),
    ])
    return phases


async def _activate_block(block_id: str):
    """Switch to a different content block — rebuilds the agent tree."""
    global runner, _root_agent, _active_config, _active_block_id, _active_block_path

    config = block_manager.get_block(block_id)
    block_path = block_manager.get_block_path(block_id)

    new_root = build_agents(config, block_path)
    runner = Runner(agent=new_root, app_name=APP_NAME, session_service=session_service)

    _root_agent = new_root
    _active_config = config
    _active_block_id = block_id
    _active_block_path = block_path

    # Reset session (new block = new conversation context)
    _state["session_id"] = None
    _state["status"] = "idle"
    _state["current_proposal"] = None
    _state["production_result"] = None
    _state["error"] = None

    block_manager.set_active_block(block_id)


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
    """Parse the agent's proposal text into structured data."""
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
        "proposals": _state["current_proposal"],
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
    """Parse up to 6 proposals from agent response into a list of dicts."""
    splits = re.split(
        r'(?:^|\n)\s*(?:#{1,4}\s*)?(?:\*\*)?(?:Option|Proposal|Topic)\s*(\d)[\s.:—\-\)]+',
        raw_text, flags=re.IGNORECASE
    )

    proposals = []

    if len(splits) >= 3:
        for i in range(1, len(splits), 2):
            if i + 1 < len(splits):
                chunk = splits[i + 1]
                p = _parse_proposal(chunk)
                if p["topic"] != "Untitled Proposal":
                    proposals.append(p)

    # Fallback: try splitting by numbered bold items
    if len(proposals) < 2:
        proposals = []
        bold_topics = re.findall(
            r'(?:^|\n)\s*\d+[\.\)]\s*\*\*([^*]{5,80})\*\*',
            raw_text
        )
        if len(bold_topics) >= 2:
            for bt in bold_topics[:6]:
                topic_clean = bt.strip().rstrip('.')
                idx = raw_text.find(bt)
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
                    "why": why or "A compelling topic for this block.",
                    "visual": "",
                    "source": source,
                    "trending": trending or "Trending",
                    "raw": chunk,
                })

    # Final fallback: parse as a single proposal
    if len(proposals) < 1:
        single = _parse_proposal(raw_text)
        proposals = [single]

    return proposals[:6]


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
            "and give me exactly 6 topic options to choose from. "
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
            "None of those grab me. Give me 6 completely different topic options. "
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


@app.post("/api/chat")
async def chat(request: Request):
    """Conversational endpoint for WhatsApp / Twilio.
    Sends the user's message to Kira and returns the reply.
    If the conversation leads to a confirmed topic, production
    starts in the background and the hand-off reply is returned
    immediately."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON."}, status_code=400)

    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "No message provided."}, status_code=400)

    if _state["status"] == "producing":
        return {"reply": "Still working on the current video — I'll message you when it's live!"}

    _state["error"] = None

    session = await _get_or_create_session()
    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=message)],
    )

    reply_parts: list[str] = []
    all_text: list[str] = []
    reply_ready = asyncio.Event()
    production_launched = False

    async def _process():
        nonlocal production_launched

        phases = _get_phases()
        phase_order = [p[0] for p in phases]
        phase_labels = {p[0]: p[1] for p in phases}
        current_phase_idx = 0

        try:
            async for event in runner.run_async(
                user_id=_state["user_id"],
                session_id=session.id,
                new_message=content,
            ):
                if not event.content or not event.content.parts:
                    continue

                for part in event.content.parts:
                    if part.text:
                        all_text.append(part.text)
                        if not production_launched:
                            reply_parts.append(part.text)

                    if hasattr(part, "function_call") and part.function_call:
                        tool_name = part.function_call.name

                        if tool_name in _PRODUCTION_TOOLS and not production_launched:
                            production_launched = True
                            _state["status"] = "producing"
                            _state["production_result"] = None
                            await event_bus.clear()
                            for pid, pname in phases:
                                await event_bus.emit(ProductionEvent(
                                    phase=pid, status="pending", detail=pname,
                                ))
                            reply_ready.set()

                        if production_launched and tool_name in _TOOL_TO_PHASE:
                            phase = _TOOL_TO_PHASE[tool_name]
                            if phase in phase_order:
                                phase_idx = phase_order.index(phase)
                                for i in range(current_phase_idx, phase_idx):
                                    if phase_order[i] in phase_labels:
                                        await event_bus.emit(ProductionEvent(
                                            phase=phase_order[i],
                                            status="completed",
                                            detail=f"{phase_labels[phase_order[i]]} — done",
                                            progress=1.0,
                                        ))
                                await event_bus.emit(ProductionEvent(
                                    phase=phase,
                                    status="in_progress",
                                    detail=f"{phase_labels[phase]} ...",
                                    progress=0.5,
                                ))
                                current_phase_idx = phase_idx

                    if (
                        production_launched
                        and hasattr(part, "function_response")
                        and part.function_response
                    ):
                        resp_name = part.function_response.name
                        if resp_name in _TOOL_TO_PHASE:
                            phase = _TOOL_TO_PHASE[resp_name]
                            if phase in phase_order:
                                result_data = part.function_response.response
                                preview = None
                                if isinstance(result_data, dict):
                                    for v in result_data.values():
                                        if isinstance(v, str) and (
                                            v.startswith("http")
                                            and any(
                                                ext in v.lower()
                                                for ext in [".png", ".jpg", ".mp4", ".webm"]
                                            )
                                        ):
                                            preview = v
                                elif isinstance(result_data, str) and result_data.startswith("http"):
                                    preview = result_data

                                await event_bus.emit(ProductionEvent(
                                    phase=phase,
                                    status="completed",
                                    detail=f"{phase_labels[phase]} — done",
                                    progress=1.0,
                                    preview_url=preview,
                                ))
                                current_phase_idx = phase_order.index(phase) + 1

            if production_launched:
                for i in range(current_phase_idx, len(phase_order)):
                    await event_bus.emit(ProductionEvent(
                        phase=phase_order[i],
                        status="completed",
                        detail=f"{phase_labels[phase_order[i]]} — done",
                        progress=1.0,
                    ))

                full_response = "\n".join(all_text)
                video_id = None
                yt_match = re.search(
                    r"(?:youtube\.com/shorts/|video[_ ]?(?:id|ID)[:\s]*)\s*([A-Za-z0-9_-]{11})",
                    full_response,
                )
                if yt_match:
                    video_id = yt_match.group(1)

                _state["production_result"] = {
                    "response": full_response,
                    "video_id": video_id,
                    "youtube_url": f"https://youtube.com/shorts/{video_id}" if video_id else None,
                }
                _state["status"] = "done"
                await event_bus.emit(ProductionEvent(
                    phase="done",
                    status="completed",
                    detail="Production complete!",
                    progress=1.0,
                    preview_url=f"https://youtube.com/shorts/{video_id}" if video_id else None,
                ))

        except Exception as e:
            logger.error(f"Chat/production failed: {e}\n{traceback.format_exc()}")
            if production_launched:
                _state["status"] = "error"
                _state["error"] = str(e)
                await event_bus.emit(ProductionEvent(
                    phase="error",
                    status="error",
                    detail="Production failed",
                    error_message=str(e),
                ))
        finally:
            if not reply_ready.is_set():
                reply_ready.set()

    asyncio.create_task(_process())

    try:
        await asyncio.wait_for(reply_ready.wait(), timeout=120)
    except asyncio.TimeoutError:
        return {"reply": "Hmm, taking longer than expected. Try again in a bit."}

    reply = "\n".join(reply_parts)
    if not reply:
        reply = "Something went wrong on my end. Try again?"

    return {"reply": reply, "producing": production_launched}


@app.post("/api/approve")
async def approve(request: Request):
    """Approve a chosen topic and start async production."""
    if _state["status"] != "proposed" or not _state["current_proposal"]:
        return JSONResponse(
            {"error": "No proposal to approve."},
            status_code=409,
        )

    try:
        body = await request.json()
        chosen_idx = body.get("index", 0)
    except Exception:
        chosen_idx = 0

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

    # Emit initial pending phases (dynamic based on active block)
    phases = _get_phases()
    for phase_id, phase_name in phases:
        await event_bus.emit(ProductionEvent(
            phase=phase_id,
            status="pending",
            detail=phase_name,
        ))

    asyncio.create_task(_run_production())
    return {"status": "producing", "message": "Production started."}


async def _run_production():
    """Run the full production pipeline via ADK, emitting events."""
    try:
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

        phases = _get_phases()
        phase_order = [p[0] for p in phases]
        phase_labels = {p[0]: p[1] for p in phases}
        current_phase_idx = 0

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

                    if hasattr(part, 'function_call') and part.function_call:
                        tool_name = part.function_call.name
                        if tool_name in _TOOL_TO_PHASE:
                            phase = _TOOL_TO_PHASE[tool_name]
                            if phase in phase_order:
                                phase_idx = phase_order.index(phase)
                                for i in range(current_phase_idx, phase_idx):
                                    if phase_order[i] in phase_labels:
                                        await event_bus.emit(ProductionEvent(
                                            phase=phase_order[i],
                                            status="completed",
                                            detail=f"{phase_labels[phase_order[i]]} — done",
                                            progress=1.0,
                                        ))
                                await event_bus.emit(ProductionEvent(
                                    phase=phase,
                                    status="in_progress",
                                    detail=f"{phase_labels[phase]} ...",
                                    progress=0.5,
                                ))
                                current_phase_idx = phase_idx

                    if hasattr(part, 'function_response') and part.function_response:
                        resp_name = part.function_response.name
                        if resp_name in _TOOL_TO_PHASE:
                            phase = _TOOL_TO_PHASE[resp_name]
                            if phase in phase_order:
                                result_data = part.function_response.response
                                preview = None
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
    instruction_type = body.get("type", "standing")

    if not instruction:
        return JSONResponse({"error": "No instruction provided."}, status_code=400)

    if instruction_type == "next":
        write_memory(next_instruction=instruction)
    else:
        write_memory(standing_instruction=instruction)

    try:
        await _send_message(
            f"User steering: {instruction}. "
            "Save this to memory and acknowledge."
        )
    except Exception:
        pass

    return {"status": "ok", "instruction": instruction, "type": instruction_type}


@app.delete("/api/taste/{index}")
async def remove_taste(index: int):
    """Remove a standing instruction by index."""
    memory = read_memory()
    standing = memory.get("standing", [])
    if 0 <= index < len(standing):
        removed = standing.pop(index)
        memory["standing"] = standing
        save_memory(memory)
        return {"status": "ok", "removed": removed}
    return JSONResponse({"error": "Invalid index."}, status_code=404)


# ── Block API ────────────────────────────────────────────────────

@app.get("/api/blocks")
async def list_blocks_route():
    """List all content blocks."""
    return {"blocks": block_manager.list_blocks()}


@app.get("/api/blocks/active")
async def get_active_block_route():
    """Return the currently active block's config."""
    return _active_config


@app.post("/api/blocks")
async def create_block_route(request: Request):
    """Create a new content block using the meta LLM."""
    if _state["status"] == "producing":
        return JSONResponse(
            {"error": "Cannot create a block while production is in progress."},
            status_code=409,
        )

    try:
        form_data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON."}, status_code=400)

    name = form_data.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "Block name is required."}, status_code=400)
    if not form_data.get("description", "").strip():
        return JSONResponse({"error": "Block description is required."}, status_code=400)

    try:
        config = await block_manager.create_block(form_data)
        # Auto-activate the new block
        await _activate_block(config["id"])
        return {"block": config, "message": "Block created and activated."}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except Exception as e:
        logger.error(f"Block creation failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/blocks/{block_id}/activate")
async def activate_block_route(block_id: str):
    """Switch to a different content block."""
    if _state["status"] == "producing":
        return JSONResponse(
            {"error": "Cannot switch blocks while production is in progress."},
            status_code=409,
        )

    try:
        await _activate_block(block_id)
        return {"status": "ok", "active_block": _active_config["name"]}
    except FileNotFoundError:
        return JSONResponse({"error": f"Block '{block_id}' not found."}, status_code=404)


@app.delete("/api/blocks/{block_id}")
async def delete_block_route(block_id: str):
    """Delete a content block."""
    if _state["status"] == "producing":
        return JSONResponse(
            {"error": "Cannot delete a block while production is in progress."},
            status_code=409,
        )

    blocks = block_manager.list_blocks_raw()
    if len(blocks) <= 1:
        return JSONResponse({"error": "Cannot delete the last block."}, status_code=409)

    try:
        was_active = block_id == _active_block_id
        block_manager.delete_block(block_id)
        if was_active:
            new_active = block_manager.get_active_block_id()
            if new_active:
                await _activate_block(new_active)
        return {"status": "ok", "message": f"Block '{block_id}' deleted."}
    except FileNotFoundError:
        return JSONResponse({"error": f"Block '{block_id}' not found."}, status_code=404)


# ── Entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
