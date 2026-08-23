import os

from google.adk.agents import LlmAgent
from google.adk.tools import google_search
from google.genai import types

from .tools.memory import read_memory, write_memory
from .tools.trends import search_trends
from .tools.image_gen import generate_image
from .tools.video_gen import generate_video
from .tools.youtube import upload_to_youtube

MODEL = "gemini-3.5-flash"

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")


def _load_prompt(filename: str) -> str:
    with open(os.path.join(PROMPTS_DIR, filename)) as f:
        return f.read()


# ──────────────────────────────────────────────
# TOOL AGENT: Web Trends Search (single-turn sub-agent)
# ──────────────────────────────────────────────
# Wraps ADK's built-in google_search (live Gemini grounding) so Kira can
# call it like a tool. Used as a fallback when the YouTube-specific
# pytrends signal (search_trends) is empty or rate-limited.
# mode="single_turn" makes this behave like a callable tool that returns
# a result to the caller, rather than a full conversational handoff.

web_trends_agent = LlmAgent(
    name="web_trends_search",
    model=MODEL,
    mode="single_turn",
    description=(
        "Searches the live web for current trending space, astronomy, "
        "and cosmos news. Use this when search_trends() (YouTube-specific "
        "trends) returns empty or rate-limited results."
    ),
    instruction=_load_prompt("web_trends_agent.md"),
    tools=[google_search],
    # Gemini requires this explicit flag when a built-in tool
    # (google_search) is combined with function calling in the same
    # request, which ADK does under the hood for single_turn sub-agents.
    generate_content_config=types.GenerateContentConfig(
        tool_config=types.ToolConfig(
            include_server_side_tool_invocations=True,
        ),
    ),
)

# ──────────────────────────────────────────────
# AGENT 2: Execution Agent (sub-agent)
# ──────────────────────────────────────────────

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
    instruction=_load_prompt("execution_agent.md"),
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

root_agent = LlmAgent(
    name="kira",
    model=MODEL,
    description="Kira — autonomous content strategist for a YouTube Shorts channel about space and cosmos.",
    instruction=_load_prompt("research_agent.md"),
    tools=[
        search_trends,
        read_memory,
        write_memory,
    ],
    sub_agents=[execution_agent, web_trends_agent],
)
