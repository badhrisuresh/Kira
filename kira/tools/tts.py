import fal_client

# Fixed voice / delivery — only the spoken prompt varies per video.
_DEFAULT_VOICE = "Charon"
_DEFAULT_STYLE = (
    "Deep, warm, authoritative male voice with a calm, measured delivery, "
    "inspired by the gravitas of Morgan Freeman. Cinematic documentary "
    "narration in the style of National Geographic or Discovery science "
    "documentaries—intelligent, immersive, and quietly captivating."
)


def generate_voiceover(prompt: str) -> str:
    """Generate a full-video voiceover MP3 from narration text using
    fal-ai/gemini-3.1-flash-tts (Charon voice).

    Args:
        prompt: The complete spoken narration for the Short — all shots
            concatenated in order as one continuous VO script. Do NOT
            include stage directions, SFX notes, or shot labels; only
            the words that should be spoken.

    Returns: URL of the generated MP3. Pass to fit_and_mux_audio()
        along with the concatenated video and background music URL."""
    result = fal_client.subscribe(
        "fal-ai/gemini-3.1-flash-tts",
        arguments={
            "voice": _DEFAULT_VOICE,
            "prompt": prompt,
            "temperature": 1,
            "language_code": "English (US)",
            "output_format": "mp3",
            "style_instructions": _DEFAULT_STYLE,
        },
        with_logs=True,
        on_queue_update=lambda update: None,
    )
    return result["audio"]["url"]
