"""Per-user memory for Kira.

Memory is stored as a JSONB column on the users table in Postgres.
The server loads a user's memory before each agent run and flushes it
back after. The tools here just read/write a module-level dict —
no async/sync boundary issues.

When Postgres is not configured, falls back to the legacy per-block
memory.json file (local or GCS).
"""

import json
import logging
import os
from datetime import date

from .. import db as db_mod
from .. import storage

log = logging.getLogger(__name__)

_DEFAULT_MEMORY = {"topics": [], "standing": [], "next": None}

# ── Per-user in-memory cache (set by server.py before each run) ──

_current_phone: str = ""
_current_memory: dict = {}

# ── Legacy fallback path (used when DB is not configured) ────────
_FALLBACK_MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory.json")
_active_memory_path = _FALLBACK_MEMORY_PATH


def configure(block_path: str):
    """Legacy: set block-specific memory path for file-based fallback."""
    global _active_memory_path
    _active_memory_path = os.path.join(block_path, "memory.json")


def configure_user(phone: str, memory_data: dict):
    """Called by server.py before each agent run to load the user's memory."""
    global _current_phone, _current_memory
    _current_phone = phone
    _current_memory = dict(memory_data) if memory_data else dict(_DEFAULT_MEMORY)
    log.info("[MEMORY] Loaded for user=%s | topics=%d | standing=%d",
             phone, len(_current_memory.get("topics", [])),
             len(_current_memory.get("standing", [])))


def get_current_memory() -> tuple:
    """Called by server.py after agent run to flush memory back to DB.
    Returns (phone, memory_dict)."""
    return _current_phone, dict(_current_memory)


def read_memory() -> dict:
    """Read Kira's memory of all past topics posted, user steering
    instructions (both standing and one-time), and any performance notes.
    Returns a dict with keys: topics (list of past posts with topic,
    video_id, date), standing (list of permanent rules), next (one-time
    topic request or None)."""
    # Per-user memory (DB-backed)
    if _current_phone and _current_memory:
        return dict(_current_memory)

    # Legacy fallback: file or GCS
    if storage.is_enabled():
        return storage.read_json(dict(_DEFAULT_MEMORY))
    if not os.path.exists(_active_memory_path):
        return dict(_DEFAULT_MEMORY)
    with open(_active_memory_path) as f:
        return json.load(f)


def save_memory(memory: dict) -> None:
    """Persist a full memory dict. When per-user memory is active,
    updates the in-memory cache (server.py flushes to DB later)."""
    global _current_memory
    if _current_phone:
        _current_memory = memory
        return

    # Legacy fallback
    if storage.is_enabled():
        storage.write_json(memory)
    else:
        with open(_active_memory_path, "w") as f:
            json.dump(memory, f, indent=2)


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

    save_memory(memory)

    return "Memory updated successfully."
