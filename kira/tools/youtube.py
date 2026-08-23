import os
import pickle
import urllib.request

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "token.pickle")


def upload_to_youtube(video_url: str, title: str, description: str) -> str:
    """Download a video from URL and upload it to YouTube as a private
    Short. Title should include #Shorts for YouTube classification.

    Args:
        video_url: URL of the video file (from generate_video).
        title: YouTube video title, under 60 characters, include #Shorts.
        description: YouTube description with source citation.

    Returns: YouTube video ID string."""
    local_path = "/tmp/kira_upload.mp4"
    urllib.request.urlretrieve(video_url, local_path)

    with open(TOKEN_FILE, "rb") as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
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
            "status": {"privacyStatus": "private"},
        },
        media_body=MediaFileUpload(local_path, resumable=True),
    )
    response = request.execute()
    return response["id"]
