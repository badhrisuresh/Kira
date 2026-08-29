import base64
import json
import logging
import os
import pickle
import tempfile
import time as _time
import urllib.request

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

log = logging.getLogger(__name__)

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "token.pickle")


def _load_credentials():
    """Load YouTube OAuth credentials.

    On Cloud Run the filesystem is ephemeral, so the token is passed as
    a base64-encoded JSON string (the output of `token.json`, base64'd)
    via YOUTUBE_TOKEN_JSON. Locally, falls back to the token.pickle file
    produced by the OAuth flow.
    """
    token_json = os.environ.get("YOUTUBE_TOKEN_JSON")
    if token_json:
        info = json.loads(base64.b64decode(token_json))
        return Credentials.from_authorized_user_info(info)
    with open(TOKEN_FILE, "rb") as f:
        return pickle.load(f)


def upload_to_youtube(video_url: str, title: str, description: str) -> str:
    """Download a video from URL and upload it to YouTube as a private
    Short. Title should include #Shorts for YouTube classification.

    Args:
        video_url: URL of the video file (from generate_video).
        title: YouTube video title, under 60 characters, include #Shorts.
        description: YouTube description with source citation.

    Returns: YouTube video ID string."""
    log.info("[YOUTUBE] Starting upload | title=%s | source=%s", title, video_url[:80])
    t0 = _time.time()

    # Accept both URLs and local file paths (from concat_videos).
    if os.path.isfile(video_url):
        local_path = video_url
    else:
        local_path = os.path.join(tempfile.gettempdir(), "kira_upload.mp4")
        log.info("[YOUTUBE] Downloading video from URL...")
        urllib.request.urlretrieve(video_url, local_path)
        log.info("[YOUTUBE] Download complete | elapsed=%.1fs", _time.time() - t0)

    creds = _load_credentials()
    if creds.expired and creds.refresh_token:
        log.info("[YOUTUBE] Refreshing OAuth credentials...")
        creds.refresh(Request())
        if not os.environ.get("YOUTUBE_TOKEN_JSON"):
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)

    youtube = build("youtube", "v3", credentials=creds)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "private",
                "containsSyntheticMedia": True,
            },
        },
        media_body=MediaFileUpload(local_path, resumable=True),
    )
    response = request.execute()
    video_id = response["id"]
    log.info("[YOUTUBE] Upload success | video_id=%s | url=https://youtube.com/shorts/%s | elapsed=%.1fs",
             video_id, video_id, _time.time() - t0)
    return video_id
