import os
import subprocess
import uuid

import requests


def fit_and_mux_voiceover(video_path: str, audio_url: str) -> str:
    """Speed-adjust a TTS voiceover to match the video length, then mix
    it over the video's existing SFX bed.

    Call this AFTER concat_videos() and generate_voiceover().

    Args:
        video_path: Local path to the concatenated video
            (from concat_videos).
        audio_url: TTS MP3 URL (from generate_voiceover).

    Returns: Local path to a new mp4 with VO mixed in. Pass this to
        upload_to_youtube()."""
    audio_path = f"/tmp/kira_vo_{uuid.uuid4().hex[:6]}.mp3"
    _download(audio_url, audio_path)

    video_dur = _probe_duration(video_path)
    audio_dur = _probe_duration(audio_path)

    # tempo > 1 speeds audio up (shortens); < 1 slows it down.
    tempo = audio_dur / video_dur if video_dur > 0 else 1.0
    # Avoid tiny no-op adjustments / divide-by-zero weirdness.
    if abs(tempo - 1.0) < 0.03:
        tempo = 1.0

    atempo = _atempo_filter_chain(tempo)
    output_path = f"/tmp/kira_vo_mux_{uuid.uuid4().hex[:6]}.mp4"

    if _has_audio(video_path):
        # Mix: VO at full level, SFX bed quieter underneath.
        # duration=first keeps output length = video length.
        filter_complex = (
            f"[1:a]{atempo},volume=1.0[vo];"
            f"[0:a]volume=0.35[sfx];"
            f"[vo][sfx]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ]
    else:
        # Video has no SFX bed — just attach speed-adjusted VO.
        filter_complex = f"[1:a]{atempo}[a]"
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ]

    subprocess.run(cmd, check=True, capture_output=True)

    os.remove(audio_path)
    return output_path


def _atempo_filter_chain(tempo: float) -> str:
    """Build an atempo chain. Each atempo filter only accepts 0.5–2.0."""
    tempo = max(0.5, min(tempo, 4.0))  # hard clamp for sanity
    factors: list[float] = []
    remaining = tempo
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(round(remaining, 4))
    return ",".join(f"atempo={f}" for f in factors)


def _has_audio(path: str) -> bool:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            path,
        ],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _probe_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _download(url: str, dest: str) -> None:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        f.write(resp.content)
