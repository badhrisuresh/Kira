"""GCS-backed persistence for memory.json.

Cloud Run's filesystem is ephemeral, so memory.json can't live on local
disk in production. Set GCS_BUCKET_NAME to read/write it from a GCS
bucket instead; when unset (local dev), callers fall back to the local
file.
"""

import json
import os

_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
_BLOB_NAME = os.environ.get("GCS_MEMORY_BLOB", "memory.json")

_bucket = None


def _get_bucket():
    global _bucket
    if _bucket is None:
        from google.cloud import storage

        _bucket = storage.Client().bucket(_BUCKET_NAME)
    return _bucket


def is_enabled() -> bool:
    return bool(_BUCKET_NAME)


def read_json(default: dict) -> dict:
    """Read memory.json from GCS, or return `default` if it doesn't exist yet."""
    blob = _get_bucket().blob(_BLOB_NAME)
    if not blob.exists():
        return default
    return json.loads(blob.download_as_text())


def write_json(data: dict) -> None:
    """Write memory.json to GCS."""
    blob = _get_bucket().blob(_BLOB_NAME)
    blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")
