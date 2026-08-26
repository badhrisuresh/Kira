import fal_client

_DEFAULT_VOICE = "Kore"
_DEFAULT_STYLE = (
    "Clear, warm female voice with a calm, confident, measured delivery. "
    "Cinematic documentary narration in the style of National Geographic "
    "or Discovery science documentaries—intelligent, immersive, and "
    "quietly captivating."
)

_active_style = _DEFAULT_STYLE


def configure(voice_style: str):
    global _active_style
    _active_style = voice_style or _DEFAULT_STYLE


def generate_voiceover(prompt: str) -> str:
    """Generate a full-video voiceover MP3 from narration text using
    fal-ai/gemini-3.1-flash-tts (Kore voice).

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
            "style_instructions": _active_style,
        },
        with_logs=True,
        on_queue_update=lambda update: None,
    )
    return result["audio"]["url"]
