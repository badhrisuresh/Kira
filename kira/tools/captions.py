"""Word-synced burned-in captions: bold white text with a dark outline,
centered in the lower-middle third of the frame — the common short-form
"POV" caption style.

Words are transcribed from the voiceover with fal.ai Whisper (word-level
timestamps), grouped into short on-screen chunks, and rendered as an ASS
subtitle file for ffmpeg's `ass` filter to burn in.
"""

import difflib
import os
import re

import fal_client


def _normalize(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


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


def reconcile_with_script(words: list[dict], script: str) -> list[dict]:
    """Snap ASR words to the known narration script, keeping ASR timing
    but guaranteeing captions show exactly the approved script text —
    a word-level forced alignment isn't available on fal.ai, so this
    aligns Whisper's transcript to the ground-truth script text via
    sequence matching instead of trusting its (possibly misheard) words.

    Falls back to the raw ASR words if there's nothing to align against.
    """
    script_tokens = re.findall(r"\S+", script)
    if not script_tokens or not words:
        return words

    asr_norm = [_normalize(w["text"]) for w in words]
    script_norm = [_normalize(w) for w in script_tokens]

    matcher = difflib.SequenceMatcher(None, asr_norm, script_norm, autojunk=False)
    aligned: list[dict] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                w = words[i1 + offset]
                aligned.append({
                    "text": script_tokens[j1 + offset],
                    "start": w["start"],
                    "end": w["end"],
                })
        elif tag == "replace":
            # Different word counts on each side (ASR merged/split a
            # word) — spread the ASR segment's time span evenly across
            # the script's words instead of dropping the mismatch.
            span_start = words[i1]["start"]
            span_end = words[i2 - 1]["end"]
            count = j2 - j1
            step = (span_end - span_start) / count if span_end > span_start else 0.0
            for k in range(count):
                aligned.append({
                    "text": script_tokens[j1 + k],
                    "start": span_start + step * k,
                    "end": span_start + step * (k + 1) if step else span_end,
                })
        elif tag == "insert":
            # Script has words ASR didn't hear at all — anchor them to
            # the last known timestamp so they still appear in place.
            anchor = aligned[-1]["end"] if aligned else words[min(i1, len(words) - 1)]["start"]
            for tok in script_tokens[j1:j2]:
                aligned.append({"text": tok, "start": anchor, "end": anchor})
        # tag == "delete": ASR heard extra words (fillers, stutters) not
        # in the script — drop them, the script is the source of truth.

    return aligned or words


def group_into_captions(
    words: list[dict],
    max_words: int = 4,
    max_chars: int = 24,
) -> list[dict]:
    """Group words into short on-screen caption chunks (a few words each,
    breaking early on sentence-ending punctuation).  Each group carries
    its individual word timings so the renderer can highlight one word
    at a time."""
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
            "words": group,
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
Style: Caption,Montserrat,{fontsize},&H00FFFFFF,&H00000000,&H80000000,-1,0,1,{outline},1,5,60,60,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def get_fontsdir() -> str:
    """Return the path to the bundled fonts directory for ffmpeg's ass filter."""
    return _FONTS_DIR

_HIGHLIGHT_COLOR = "&H0000FFFF&"
_NORMAL_COLOR = "&H00FFFFFF&"


def _sanitize(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")


def write_ass_file(
    captions: list[dict],
    width: int,
    height: int,
    path: str,
    time_scale: float = 1.0,
) -> None:
    """Write an ASS subtitle file with word-by-word yellow highlight,
    Montserrat Bold, centered vertically.

    Args:
        time_scale: multiply every caption timestamp by this factor. Use
            this when the captioned audio was later time-stretched (e.g.
            by atempo) to fit the final video length — pass 1 / tempo so
            captions stay in sync with the speech.
    """
    fontsize = round(height * 0.07)
    outline = max(3, round(fontsize * 0.08))

    lines = [_ASS_TEMPLATE.format(
        width=width, height=height, fontsize=fontsize, outline=outline,
    )]
    for cap in captions:
        cap_words = cap.get("words", [])
        if not cap_words:
            start = cap["start"] * time_scale
            end = cap["end"] * time_scale
            if end <= start:
                end = start + 0.3
            text = _sanitize(cap["text"])
            lines.append(
                f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
                f"Caption,,0,0,0,,{text}\n"
            )
            continue

        for wi, active_word in enumerate(cap_words):
            w_start = active_word["start"] * time_scale
            if wi + 1 < len(cap_words):
                w_end = cap_words[wi + 1]["start"] * time_scale
            else:
                w_end = cap["end"] * time_scale
            if w_end <= w_start:
                w_end = w_start + 0.3

            parts = []
            for wj, w in enumerate(cap_words):
                clean = _sanitize(w["text"])
                if wj == wi:
                    parts.append("{\\c" + _HIGHLIGHT_COLOR + "}" + clean)
                else:
                    parts.append("{\\c" + _NORMAL_COLOR + "}" + clean)
            text = " ".join(parts)

            lines.append(
                f"Dialogue: 0,{_ass_timestamp(w_start)},{_ass_timestamp(w_end)},"
                f"Caption,,0,0,0,,{text}\n"
            )

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
