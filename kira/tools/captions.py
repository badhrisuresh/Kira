"""Word-synced burned-in captions: bold white text with a dark outline,
centered in the lower-middle third of the frame — the common short-form
"POV" caption style.

Words are transcribed from the voiceover with fal.ai Whisper (word-level
timestamps), grouped into short on-screen chunks, and rendered as an ASS
subtitle file for ffmpeg's `ass` filter to burn in.
"""

import re

import fal_client


def transcribe_words(audio_path: str) -> list[dict]:
    """Transcribe an audio file to word-level timestamps via fal.ai Whisper.

    Returns a list of {"text": str, "start": float, "end": float} in the
    audio's own (untouched) timeline — the caller is responsible for
    rescaling if that audio is later time-stretched (e.g. via atempo)."""
    audio_url = fal_client.upload_file(audio_path)
    result = fal_client.subscribe(
        "fal-ai/whisper",
        arguments={
            "audio_url": audio_url,
            "task": "transcribe",
            "chunk_level": "word",
        },
        with_logs=True,
        on_queue_update=lambda update: None,
    )
    chunks = result.get("chunks") or []
    words = []
    for c in chunks:
        text = (c.get("text") or "").strip()
        ts = c.get("timestamp") or [None, None]
        if not text or ts[0] is None or ts[1] is None:
            continue
        words.append({"text": text, "start": float(ts[0]), "end": float(ts[1])})
    return words


def group_into_captions(
    words: list[dict],
    max_words: int = 4,
    max_chars: int = 24,
) -> list[dict]:
    """Group words into short on-screen caption chunks (a few words each,
    breaking early on sentence-ending punctuation)."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0

    for w in words:
        word_len = len(w["text"]) + 1
        if current and (len(current) >= max_words or current_len + word_len > max_chars):
            groups.append(current)
            current, current_len = [], 0
        current.append(w)
        current_len += word_len
        if re.search(r"[.!?]$", w["text"]):
            groups.append(current)
            current, current_len = [], 0

    if current:
        groups.append(current)

    return [
        {
            "text": " ".join(w["text"] for w in group),
            "start": group[0]["start"],
            "end": group[-1]["end"],
        }
        for group in groups
    ]


def _ass_timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    cs_total = round(seconds * 100)
    hours, rem = divmod(cs_total, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, centis = divmod(rem, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


_ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,{fontsize},&H00FFFFFF,&H00000000,&H00000000,-1,0,1,{outline},0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass_file(
    captions: list[dict],
    width: int,
    height: int,
    path: str,
    time_scale: float = 1.0,
) -> None:
    """Write an ASS subtitle file styled as bold, centered, outlined
    captions positioned in the lower-middle third of the frame.

    Args:
        time_scale: multiply every caption timestamp by this factor. Use
            this when the captioned audio was later time-stretched (e.g.
            by atempo) to fit the final video length — pass 1 / tempo so
            captions stay in sync with the speech.
    """
    fontsize = round(height * 0.055)
    outline = max(2, round(fontsize * 0.07))
    margin_v = round(height * 0.38)

    lines = [_ASS_TEMPLATE.format(
        width=width, height=height, fontsize=fontsize,
        outline=outline, margin_v=margin_v,
    )]
    for cap in captions:
        start = cap["start"] * time_scale
        end = cap["end"] * time_scale
        if end <= start:
            end = start + 0.3
        text = cap["text"].replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
            f"Caption,,0,0,0,,{text}\n"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
