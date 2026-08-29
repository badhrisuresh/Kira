import logging
import time

import fal_client

log = logging.getLogger(__name__)


def generate_video(image_urls: list[str], prompt: str, duration: int = 8) -> str:
    """Generate a video clip from 1+ reference images using Gemini Omni
    Flash reference-to-video on fal.ai.

    Args:
        image_urls: List of reference image URLs (from generate_image).
        prompt: Motion and visual prompt. Describe camera movement and
            subject action only — no audio, SFX, or narration (clip
            audio is discarded; TTS and background music are added
            later). Always specify 9:16 vertical.
        duration: Clip length in seconds, integer 3-10. Default 8.
            Choose based on the shot's role in the edit:
            shorter for quick hooks or cuts, longer for payoff or
            establishing shots.

    Returns: URL of the generated video clip."""
    try:
        duration_int = int(duration)
    except (TypeError, ValueError):
        duration_int = 8
    if duration_int < 3 or duration_int > 10:
        duration_int = 8

    log.info("[VIDEO_GEN] Starting video generation | images=%d | duration=%ds | prompt=%s",
             len(image_urls), duration_int, prompt[:120])
    t0 = time.time()
    try:
        result = fal_client.subscribe(
            "google/gemini-omni-flash/reference-to-video",
            arguments={
                "image_urls": image_urls,
                "prompt": prompt,
                "duration": duration_int,
                "aspect_ratio": "9:16",
            },
            with_logs=True,
            on_queue_update=lambda update: None,
        )
        url = result["video"]["url"]
        log.info("[VIDEO_GEN] Success | url=%s | elapsed=%.1fs", url[:80], time.time() - t0)
        return url
    except Exception as e:
        log.error("[VIDEO_GEN] Failed | error=%s | elapsed=%.1fs", e, time.time() - t0)
        raise
