import os
import subprocess
import uuid

import requests


def concat_videos(video_urls: list[str]) -> str:
    """Download multiple video clips and concatenate them into a single
    video file using ffmpeg. Use this after generating all shots with
    generate_video() to assemble the final YouTube Short.

    Args:
        video_urls: List of video URLs in chronological shot order
            (from generate_video calls). Must have at least 2 URLs.

    Returns: Local file path of the concatenated video (e.g.
        /tmp/kira_final_abc123.mp4). Pass this path directly to
        upload_to_youtube()."""
    if len(video_urls) == 1:
        # Single clip — just download, no concat needed.
        path = f"/tmp/kira_final_{uuid.uuid4().hex[:6]}.mp4"
        _download(video_urls[0], path)
        return path

    # Download all clips.
    clip_paths = []
    for i, url in enumerate(video_urls):
        path = f"/tmp/kira_clip_{i}_{uuid.uuid4().hex[:6]}.mp4"
        _download(url, path)
        clip_paths.append(path)

    # Build ffmpeg concat demuxer file.
    concat_list = f"/tmp/kira_concat_{uuid.uuid4().hex[:6]}.txt"
    with open(concat_list, "w") as f:
        for path in clip_paths:
            f.write(f"file '{path}'\n")

    output_path = f"/tmp/kira_final_{uuid.uuid4().hex[:6]}.mp4"

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            output_path,
        ],
        check=True,
        capture_output=True,
    )

    # Cleanup intermediates.
    for path in clip_paths:
        os.remove(path)
    os.remove(concat_list)

    return output_path


def _download(url: str, dest: str) -> None:
    """Download a file from URL using requests (bundled SSL certs)."""
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        f.write(resp.content)
