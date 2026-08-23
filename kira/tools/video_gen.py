import fal_client


def generate_video(image_urls: list[str], prompt: str, duration: str = "8") -> str:
    """Generate a video clip with native audio from 1-3 reference images
    using Veo 3.1 on fal.ai.

    Args:
        image_urls: List of 1-3 reference image URLs (from generate_image).
        prompt: Motion and audio prompt. Describe camera movement,
            subject action, sound design, and narration.
            Always specify 9:16 vertical.
        duration: Clip length — "4", "6", or "8" seconds. Default "8".
            Choose based on the shot's role in the edit:
            4s for quick hooks or cuts, 6s for mid beats, 8s for
            payoff or establishing shots.

    Returns: URL of the generated video clip with audio."""
    if duration not in ("4", "6", "8"):
        duration = "8"

    result = fal_client.subscribe(
        "fal-ai/veo3.1/reference-to-video",
        arguments={
            "image_urls": image_urls,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "generate_audio": True,
        },
        with_logs=True,
        on_queue_update=lambda update: None,
    )

    return result["video"]["url"]
