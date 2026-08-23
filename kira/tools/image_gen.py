import fal_client


def generate_image(prompt: str) -> str:
    """Generate a reference image from a detailed text prompt using
    Nano Banana Pro on fal.ai. Always include '9:16 vertical' in the
    prompt for YouTube Shorts format. Returns a URL that can be passed
    to generate_video()."""
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
        return "ERROR: No image was generated. Try a different prompt."

    return images[0]["url"]
