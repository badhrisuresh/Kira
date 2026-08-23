import json
import os
from datetime import date

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory.json")


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
