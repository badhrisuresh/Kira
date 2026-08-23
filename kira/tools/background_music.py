import random

import fal_client

from .audio_utils import probe_duration

_MUSIC_TEMPLATE = (
    "{duration} second Subtle, atmospheric ambient music with deep, warm drones, "
    "soft celestial textures, and a slow evolving pulse. Minimal and unobtrusive, "
    "leaving plenty of space for a clear cinematic voice-over., no vocals, no lyrics."
)
_NEGATIVE_PROMPT = (
    "vocals, speech, clipping, distortion, harsh noise, muddy mix, low quality"
)


def generate_background_music(video_path: str) -> str:
    """Generate ambient background music for the full Short using
    fal-ai/stable-audio-3/medium/text-to-audio.

    Probes the concatenated video for exact duration. Uses a fixed
    cinematic ambient prompt with a random seed each run.

    Args:
        video_path: Local path to the concatenated video
            (from concat_videos).

    Returns: URL of the generated MP3. Pass to fit_and_mux_audio()
        along with the TTS URL."""
    duration = probe_duration(video_path)
    duration_int = max(3, round(duration))
    prompt = _MUSIC_TEMPLATE.format(duration=duration_int)
    seed = random.randint(0, 2_147_483_647)

    result = fal_client.subscribe(
        "fal-ai/stable-audio-3/medium/text-to-audio",
        arguments={
            "prompt": prompt,
            "bitrate": "192k",
            "duration": duration_int,
            "output_format": "mp3",
            "guidance_scale": 1,
            "negative_prompt": _NEGATIVE_PROMPT,
            "num_inference_steps": 12,
            "enable_safety_checker": True,
            "enable_prompt_expansion": False,
            "seed": seed,
        },
        with_logs=True,
        on_queue_update=lambda update: None,
    )
    return result["audio"]["url"]
