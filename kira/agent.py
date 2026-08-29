import json
import logging
import os

from google.adk.agents import LlmAgent
from google.adk.tools import google_search
from google.genai import types

log = logging.getLogger(__name__)

from .tools.memory import read_memory, write_memory
from .tools import memory as memory_mod
from .tools.trends import search_trends
from .tools import trends
from .tools.image_gen import generate_image
from .tools.video_gen import generate_video
from .tools.concat_videos import concat_videos
from .tools.tts import generate_voiceover
from .tools import tts
from .tools.background_music import generate_background_music
from .tools import background_music
from .tools.mux_voiceover import fit_and_mux_audio, mux_music_only
from .tools.youtube import upload_to_youtube

MODEL = "gemini-3.5-flash"

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")


def _load_prompt(filename: str) -> str:
    with open(os.path.join(PROMPTS_DIR, filename)) as f:
        return f.read()


def _build_execution_prompt(block_config: dict) -> str:
    """Build execution agent prompt from the shared template, adjusted
    for the block's narration/caption/duration settings."""
    base = _load_prompt("execution_agent.md")

    dur_min = block_config.get("duration_min", 15)
    dur_max = block_config.get("duration_max", 20)
    base = base.replace("15-20 seconds", f"{dur_min}-{dur_max} seconds")
    base = base.replace("15-20 s", f"{dur_min}-{dur_max} s")

    narration = block_config.get("narration_enabled", True)
    captions = block_config.get("captions_enabled", True)

    if not narration:
        # Replace audio phase instructions for music-only
        base = base.replace(
            "## PHASE 5 — AUDIO\n"
            "\n"
            "1. Call generate_voiceover() with the VOICEOVER PROMPT from the\n"
            "   production plan (full narration only — the spoken words, nothing\n"
            "   else).\n"
            "\n"
            "2. Call generate_background_music() with:\n"
            "   - video_path: the concatenated video path from Phase 4\n"
            "\n"
            "   This generates ambient music matched to the video length (random\n"
            "   seed each run).\n"
            "\n"
            "3. Call fit_and_mux_audio() with:\n"
            "   - video_path: the concatenated video path from Phase 4\n"
            "   - voiceover_url: the MP3 URL from generate_voiceover()\n"
            "   - music_url: the MP3 URL from generate_background_music()\n"
            "   - script: the exact same VOICEOVER PROMPT text passed to\n"
            "     generate_voiceover() in step 1 — used to snap burned-in captions\n"
            "     to the approved narration instead of raw speech-to-text.\n"
            "\n"
            "   This discards clip audio, speed-fits TTS and music to the video\n"
            "   duration, burns in synced captions, and mixes the audio (VO\n"
            "   dominant, music quiet). Use the returned path as the final video.",
            "## PHASE 5 — AUDIO\n"
            "\n"
            "1. Call generate_background_music() with:\n"
            "   - video_path: the concatenated video path from Phase 4\n"
            "\n"
            "   This generates ambient music matched to the video length.\n"
            "\n"
            "2. Call mux_music_only() with:\n"
            "   - video_path: the concatenated video path from Phase 4\n"
            "   - music_url: the MP3 URL from generate_background_music()\n"
            "\n"
            "   This discards clip audio, speed-fits music to the video duration,\n"
            "   and mixes it in. Use the returned path as the final video.\n"
            "\n"
            "   There is NO voiceover or captions for this content block.",
        )

    if not captions and narration:
        base = base.replace(
            "burns in synced captions, and mixes the audio",
            "and mixes the audio (captions are disabled for this block)",
        )

    return base


def build_agents(block_config: dict, block_path: str) -> LlmAgent:
    """Build the full agent tree from a content block's config and prompts."""
    log.info("[AGENTS] Building agent tree | block=%s | narration=%s | trends=%s",
             block_config.get("name"), block_config.get("narration_enabled"),
             block_config.get("youtube_trends_enabled"))

    def load_block_prompt(filename: str) -> str:
        with open(os.path.join(block_path, filename)) as f:
            return f.read()

    # Configure tools with block-specific settings
    trends.configure(
        seed_keywords=block_config.get("seed_keywords", []),
        noise_terms=block_config.get("noise_terms", []),
        enabled=block_config.get("youtube_trends_enabled", True),
    )
    tts.configure(block_config.get("voice_style", ""))
    background_music.configure(block_config.get("music_style", ""))
    memory_mod.configure(block_path)

    narration_enabled = block_config.get("narration_enabled", True)

    # ── Web Trends sub-agent ─────────────────────────────────
    web_trends_agent = LlmAgent(
        name="web_trends_search",
        model=MODEL,

        description=(
            "Searches the live web for current trending news and events "
            f"relevant to: {block_config['name']}. Use this when "
            "search_trends() returns empty or rate-limited results, or "
            "when YouTube trends are not configured for this block."
        ),
        instruction=load_block_prompt("web_trends_agent.md"),
        tools=[google_search],
        generate_content_config=types.GenerateContentConfig(
            tool_config=types.ToolConfig(
                include_server_side_tool_invocations=True,
            ),
        ),
    )

    # ── Script Writer sub-agent ──────────────────────────────
    script_writer_agent = LlmAgent(
        name="script_writer",
        model=MODEL,

        description=(
            "Expert short-form video scriptwriter. Takes a creative brief "
            "and returns a complete production-ready script with beat-by-beat "
            "visuals, audio design, title, and description."
            + (" Includes narration text for each beat." if narration_enabled
               else " Visual-only — no narration lines.")
            + " Call this FIRST before production planning."
        ),
        instruction=load_block_prompt("script_writer.md"),
    )

    # ── Production Planner sub-agent ─────────────────────────
    production_planner_agent = LlmAgent(
        name="production_planner",
        model=MODEL,

        description=(
            "Video production planner. Takes a finished script and breaks it "
            "into a shot-by-shot production spec: number of shots (2-4), "
            "each shot's duration, reference image prompts, video generation "
            "prompts, and continuity notes."
            + (" Includes a single timed VOICEOVER PROMPT for TTS." if narration_enabled else "")
            + " Call this AFTER script_writer returns the script."
        ),
        instruction=load_block_prompt("production_breakdown.md"),
    )

    # ── Execution agent tools ────────────────────────────────
    exec_tools = [
        generate_image,
        generate_video,
        concat_videos,
        generate_background_music,
        upload_to_youtube,
        write_memory,
    ]
    if narration_enabled:
        exec_tools.insert(3, generate_voiceover)
        exec_tools.append(fit_and_mux_audio)
    else:
        exec_tools.append(mux_music_only)

    execution_prompt = _build_execution_prompt(block_config)

    execution_agent = LlmAgent(
        name="execution_agent",
        model=MODEL,
        description=(
            "Production agent that takes a confirmed creative brief and "
            "autonomously produces the final video: writes a script, plans "
            "shots, generates reference images, generates multi-shot video, "
            "concatenates clips, "
            + ("generates TTS voiceover and " if narration_enabled else "")
            + "background music, muxes audio, uploads to YouTube, and saves "
            "the result to memory. "
            "Transfer to this agent ONLY after the user has confirmed "
            "the topic and creative brief."
        ),
        instruction=execution_prompt,
        tools=exec_tools,
        sub_agents=[script_writer_agent, production_planner_agent],
    )

    # ── Root agent tools ─────────────────────────────────────
    root_tools = [read_memory, write_memory]
    if block_config.get("youtube_trends_enabled", True):
        root_tools.insert(0, search_trends)

    root_agent = LlmAgent(
        name="kira",
        model=MODEL,
        description=f"Kira — autonomous content strategist for: {block_config['name']}.",
        instruction=load_block_prompt("research_agent.md"),
        tools=root_tools,
        sub_agents=[execution_agent, web_trends_agent],
    )

    log.info("[AGENTS] Agent tree built | root=%s | sub_agents=[execution_agent, web_trends_search] "
             "| root_tools=%s | exec_tools=%d",
             root_agent.name, [t.__name__ for t in root_tools], len(exec_tools))
    return root_agent


# ── Default agent (built on import for backward compatibility) ───

def _load_default_agent() -> LlmAgent:
    from . import block_manager
    try:
        block_id = block_manager.get_active_block_id()
        if block_id:
            config = block_manager.get_block(block_id)
            path = block_manager.get_block_path(block_id)
            return build_agents(config, path)
    except Exception:
        pass

    # Fallback: build from legacy prompts dir (should not normally happen)
    from .tools.trends import configure as trends_configure
    trends_configure(
        seed_keywords=["black hole", "asteroid", "rocket launch", "james webb",
                       "supernova", "exoplanet", "mars planet", "meteorite",
                       "nebula", "dark matter", "space exploration", "moon landing",
                       "solar system"],
        noise_terms=["samsung", "mario", "game", "fortnite", "minecraft"],
        enabled=True,
    )
    fallback_config = {
        "name": "Space & Cosmos",
        "narration_enabled": True,
        "captions_enabled": True,
        "youtube_trends_enabled": True,
        "duration_min": 15,
        "duration_max": 20,
    }
    # Use prompts dir as block path (has the same .md files)
    return build_agents(fallback_config, PROMPTS_DIR)


root_agent = _load_default_agent()
