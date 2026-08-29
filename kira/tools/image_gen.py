import logging
import time

import fal_client

log = logging.getLogger(__name__)


def generate_image(prompt: str) -> str:
    """Generate a reference image from a detailed text prompt using
    Nano Banana Pro on fal.ai. Always include '9:16 vertical' in the
    prompt for YouTube Shorts format. Returns a URL that can be passed
    to generate_video()."""
    log.info("[IMAGE_GEN] Starting image generation | prompt=%s", prompt[:120])
    t0 = time.time()
    try:
        result = fal_client.subscribe(
            "fal-ai/nano-banana-pro",
            arguments={
                "prompt": prompt,
                "num_images": 1,
                "resolution": "1K",
                "aspect_ratio": "9:16",
                "output_format": "png",
            },
            with_logs=True,
            on_queue_update=lambda update: None,
        )

        images = result.get("images") or []
        if not images or not images[0].get("url"):
            log.warning("[IMAGE_GEN] No image returned | elapsed=%.1fs", time.time() - t0)
            return "ERROR: No image was generated. Try a different prompt."

        url = images[0]["url"]
        log.info("[IMAGE_GEN] Success | url=%s | elapsed=%.1fs", url[:80], time.time() - t0)
        return url
    except Exception as e:
        log.error("[IMAGE_GEN] Failed | error=%s | elapsed=%.1fs", e, time.time() - t0)
        raise
