import fal_client


def generate_video(image_urls: list[str], prompt: str, duration: int | str = 8) -> str:
    """Generate a video clip from 1+ reference images using Gemini Omni
    Flash reference-to-video on fal.ai.

    Args:
        image_urls: List of reference image URLs (from generate_image).
        prompt: Motion and SFX prompt. Describe camera movement,
            subject action, and ambient/SFX sound design only.
            Do not include spoken narration or voiceover — TTS is
            added later. Always specify 9:16 vertical.
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

    return result["video"]["url"]
