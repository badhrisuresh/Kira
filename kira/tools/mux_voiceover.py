import os
import uuid

import subprocess

from .audio_utils import (
    atempo_filter_chain,
    download,
    probe_duration,
    tempo_for_duration,
)

# Mix levels: VO dominant, music as quiet bed underneath.
_MUSIC_VOLUME = 0.18
_VO_VOLUME = 1.0


def fit_and_mux_audio(
    video_path: str,
    voiceover_url: str,
    music_url: str,
) -> str:
    """Discard video-clip audio, fit TTS + background music to video
    length, and mux into the final Short.

    Call this AFTER concat_videos(), generate_voiceover(), and
    generate_background_music().

    Args:
        video_path: Local path to the concatenated video (visuals only
            from the caller's perspective — any clip audio is ignored).
        voiceover_url: TTS MP3 URL (from generate_voiceover).
        music_url: Background music MP3 URL (from generate_background_music).

    Returns: Local path to the final mp4. Pass to upload_to_youtube()."""
    vo_path = f"/tmp/kira_vo_{uuid.uuid4().hex[:6]}.mp3"
    music_path = f"/tmp/kira_music_{uuid.uuid4().hex[:6]}.mp3"
    download(voiceover_url, vo_path)
    download(music_url, music_path)

    video_dur = probe_duration(video_path)
    vo_tempo = tempo_for_duration(probe_duration(vo_path), video_dur)
    music_tempo = tempo_for_duration(probe_duration(music_path), video_dur)

    vo_atempo = atempo_filter_chain(vo_tempo)
    music_atempo = atempo_filter_chain(music_tempo)
    output_path = f"/tmp/kira_final_mux_{uuid.uuid4().hex[:6]}.mp4"

    # Input 0: video (video stream only). Inputs 1/2: music + VO.
    filter_complex = (
        f"[1:a]{music_atempo},volume={_MUSIC_VOLUME}[music];"
        f"[2:a]{vo_atempo},volume={_VO_VOLUME}[vo];"
        f"[music][vo]amix=inputs=2:duration=first:dropout_transition=0[a]"
    )

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", music_path,
            "-i", vo_path,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ],
        check=True,
        capture_output=True,
    )

    os.remove(vo_path)
    os.remove(music_path)
    return output_path
