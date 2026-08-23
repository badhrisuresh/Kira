import os
import uuid

from google import genai
from google.genai import types


def generate_image(prompt: str) -> str:
    """Generate a reference image from a detailed text prompt using
    Nano Banana Pro (Gemini 3 Pro Image). Always include '9:16 vertical'
    in the prompt for YouTube Shorts format. Returns a URL that can be
    passed to generate_video()."""
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    response = client.models.generate_content(
        model="gemini-3-pro-image-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio="9:16",
            ),
        ),
    )

    for part in response.candidates[0].content.parts:
        if hasattr(part, "inline_data") and part.inline_data:
            image_bytes = part.inline_data.data
            filename = f"/tmp/kira_ref_{uuid.uuid4().hex[:8]}.png"
            with open(filename, "wb") as f:
                f.write(image_bytes)

            import fal_client

            url = fal_client.upload_file(filename)
            return url

    return "ERROR: No image was generated. Try a different prompt."
