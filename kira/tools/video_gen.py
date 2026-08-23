import fal_client


def generate_video(image_urls: list[str], prompt: str) -> str:
    """Generate an 8-second video with native audio from 1-3 reference
    images using Veo 3.1 on fal.ai.

    Args:
        image_urls: List of 1-3 reference image URLs (from generate_image).
        prompt: Motion and audio prompt. Describe:
            - Camera movement (slow push-in, orbit, tracking shot)
            - Character/scene action
            - Desired audio mood (dramatic drums, serene, epic orchestral)
            - Always specify 9:16 vertical.

    Returns: URL of the generated 8-second video with audio."""
    result = fal_client.subscribe(
        "fal-ai/veo3.1/reference-to-video",
        arguments={
            "image_urls": image_urls,
            "prompt": prompt,
            "duration": "8",
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "generate_audio": True,
        },
        with_logs=True,
        on_queue_update=lambda update: None,
    )

    return result["video"]["url"]
