import subprocess

import requests


def probe_duration(path: str) -> float:
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


def probe_dimensions(path: str) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    width_str, height_str = result.stdout.strip().split("x")
    return int(width_str), int(height_str)


def tempo_for_duration(source_dur: float, target_dur: float) -> float:
    """Return atempo factor to fit source audio into target duration."""
    if target_dur <= 0:
        return 1.0
    tempo = source_dur / target_dur
    if abs(tempo - 1.0) < 0.03:
        return 1.0
    return max(0.5, min(tempo, 4.0))


def atempo_filter_chain(tempo: float) -> str:
    """Build an atempo chain. Each atempo filter only accepts 0.5–2.0."""
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


def download(url: str, dest: str) -> None:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        f.write(resp.content)
