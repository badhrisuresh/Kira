import os
import uuid

import subprocess

from .audio_utils import (
    atempo_filter_chain,
    download,
    probe_dimensions,
    probe_duration,
    tempo_for_duration,
)
from .captions import (
    group_into_captions,
    reconcile_with_script,
    transcribe_words,
    write_ass_file,
)

# Mix levels: VO dominant, music as quiet bed underneath.
_MUSIC_VOLUME = 0.18
_VO_VOLUME = 1.0


def fit_and_mux_audio(
    video_path: str,
    voiceover_url: str,
    music_url: str,
    script: str = "",
) -> str:
    """Discard video-clip audio, fit TTS + background music to video
    length, burn in captions synced to the voiceover, and mux into the
    final Short.

    Call this AFTER concat_videos(), generate_voiceover(), and
    generate_background_music().

    Args:
        video_path: Local path to the concatenated video (visuals only
            from the caller's perspective — any clip audio is ignored).
        voiceover_url: TTS MP3 URL (from generate_voiceover).
        music_url: Background music MP3 URL (from generate_background_music).
        script: The exact narration text passed to generate_voiceover().
            When given, captions are snapped to this text (word timing
            still comes from the audio) instead of raw speech-to-text,
            so captions always match the approved script even if the
            transcription mishears a word. Optional but recommended —
            pass the same VOICEOVER PROMPT text used to generate the VO.

    Returns: Local path to the final mp4. Pass to upload_to_youtube()."""
    vo_path = f"/tmp/kira_vo_{uuid.uuid4().hex[:6]}.mp3"
    music_path = f"/tmp/kira_music_{uuid.uuid4().hex[:6]}.mp3"
    ass_path = f"/tmp/kira_captions_{uuid.uuid4().hex[:6]}.ass"
    download(voiceover_url, vo_path)
    download(music_url, music_path)

    video_dur = probe_duration(video_path)
    vo_tempo = tempo_for_duration(probe_duration(vo_path), video_dur)
    music_tempo = tempo_for_duration(probe_duration(music_path), video_dur)

    vo_atempo = atempo_filter_chain(vo_tempo)
    music_atempo = atempo_filter_chain(music_tempo)
    output_path = f"/tmp/kira_final_mux_{uuid.uuid4().hex[:6]}.mp4"

    # Words are timestamped against the original (un-stretched) VO audio;
    # atempo=X speeds playback by X, so a word originally at time t now
    # lands at t / vo_tempo. Caption burn-in is best-effort: if
    # transcription fails, mux proceeds without captions rather than
    # blocking the upload.
    has_captions = False
    try:
        words = transcribe_words(vo_path)
        if script:
            words = reconcile_with_script(words, script)
        captions = group_into_captions(words)
        if captions:
            width, height = probe_dimensions(video_path)
            write_ass_file(captions, width, height, ass_path, time_scale=1 / vo_tempo)
            has_captions = True
    except Exception as e:
        print(f"Captions skipped (transcription failed): {e}")

    # Input 0: video (video stream only). Inputs 1/2: music + VO.
    audio_filter = (
        f"[1:a]{music_atempo},volume={_MUSIC_VOLUME}[music];"
        f"[2:a]{vo_atempo},volume={_VO_VOLUME}[vo];"
        f"[music][vo]amix=inputs=2:duration=first:dropout_transition=0[a]"
    )

    if has_captions:
        filter_complex = f"[0:v]ass={ass_path}[v];{audio_filter}"
        video_args = ["-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    else:
        filter_complex = audio_filter
        video_args = ["-map", "0:v:0", "-c:v", "copy"]

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", music_path,
            "-i", vo_path,
            "-filter_complex", filter_complex,
            *video_args,
            "-map", "[a]",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ],
        check=True,
        capture_output=True,
    )

    os.remove(vo_path)
    os.remove(music_path)
    if os.path.exists(ass_path):
        os.remove(ass_path)
    return output_path
