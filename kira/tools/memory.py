import json
import os
from datetime import date

from .. import storage

_FALLBACK_MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory.json")
_active_memory_path = _FALLBACK_MEMORY_PATH

_DEFAULT_MEMORY = {"topics": [], "standing": [], "next": None}


def configure(block_path: str):
    global _active_memory_path
    _active_memory_path = os.path.join(block_path, "memory.json")


def read_memory() -> dict:
    """Read Kira's memory of all past topics posted, user steering
    instructions (both standing and one-time), and any performance notes.
    Returns a dict with keys: topics (list of past posts with topic,
    video_id, date), standing (list of permanent rules), next (one-time
    topic request or None)."""
    if storage.is_enabled():
        return storage.read_json(dict(_DEFAULT_MEMORY))
    if not os.path.exists(_active_memory_path):
        return dict(_DEFAULT_MEMORY)
    with open(_active_memory_path) as f:
        return json.load(f)


def save_memory(memory: dict) -> None:
    """Persist a full memory dict, routing to GCS when configured."""
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
