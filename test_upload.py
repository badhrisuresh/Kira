import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "token.pickle"


def get_credentials():
    credentials = None

    # Load saved token if it exists.
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            credentials = pickle.load(f)

    # If there's no token, or it's invalid, refresh or log in.
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            # Silent refresh, no browser needed.
            credentials.refresh(Request())
        else:
            # Only happens once, the very first time you ever run this.
            flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
            credentials = flow.run_local_server(port=0)

        # Save for next time.
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(credentials, f)

    return credentials


def upload_video(file_path, title, description):
    credentials = get_credentials()
    youtube = build("youtube", "v3", credentials=credentials)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "private"
            }
        },
        media_body=MediaFileUpload(file_path, chunksize=-1, resumable=True)
    )

    response = request.execute()
    print("Uploaded. Video ID:", response["id"])
    return response["id"]


if __name__ == "__main__":
    upload_video("test.mp4", "Ari test upload", "Testing the pipeline.")
