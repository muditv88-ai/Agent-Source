"""
gcs_storage.py

Google Cloud Storage helper for persistent project file storage.

Setup (HuggingFace Spaces — backend):
  1. Create a GCS bucket (e.g. rfp-copilot-files).
  2. Grant the service account  roles/storage.objectAdmin.
  3. In your HuggingFace Space → Settings → Variables and Secrets, add:

       GCS_BUCKET_NAME          (Variable)  e.g. rfp-copilot-files
       GCS_SIGNED_URL_EXPIRY_MINUTES  (Variable)  e.g. 60
       GCS_CREDENTIALS_JSON     (Secret)    paste the FULL contents of
                                            your service-account JSON key file

Setup (local dev):
  Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
  in your .env file.  GCS_CREDENTIALS_JSON takes priority if both are set.

File layout inside the bucket:
  projects/<project_id>/rfp_templates/<filename>
  projects/<project_id>/supplier_responses/<filename>
  projects/<project_id>/drawings/<filename>
  projects/<project_id>/misc/<filename>
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Optional

try:
    from google.cloud import storage as gcs
    from google.oauth2 import service_account
    _GCS_AVAILABLE = True
except ImportError:
    _GCS_AVAILABLE = False

_BUCKET_NAME: str = os.getenv("GCS_BUCKET_NAME", "rfp-copilot-files")
_SIGNED_URL_EXPIRY_MINUTES: int = int(os.getenv("GCS_SIGNED_URL_EXPIRY_MINUTES", "60"))


def _client() -> "gcs.Client":
    """
    Return an authenticated GCS Client.

    Auth priority:
      1. GCS_CREDENTIALS_JSON env var  — JSON string (HuggingFace Spaces secret)
      2. GOOGLE_APPLICATION_CREDENTIALS env var — file path (local dev / Cloud Run)
      3. Application Default Credentials (GCE / Cloud Run Workload Identity)
    """
    if not _GCS_AVAILABLE:
        raise RuntimeError(
            "google-cloud-storage is not installed. "
            "Run: pip install google-cloud-storage"
        )

    creds_json: Optional[str] = os.getenv("GCS_CREDENTIALS_JSON")

    if creds_json:
        # HuggingFace path: credentials are the raw JSON string stored as a secret
        try:
            info = json.loads(creds_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "GCS_CREDENTIALS_JSON is set but is not valid JSON. "
                "Make sure you pasted the full service-account key file contents."
            ) from exc

        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return gcs.Client(credentials=credentials, project=info.get("project_id"))

    # Local dev / Cloud Run — use file path or ADC
    return gcs.Client()


def _bucket() -> "gcs.Bucket":
    return _client().bucket(_BUCKET_NAME)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_file(
    project_id: str,
    category: str,          # rfp_templates | supplier_responses | drawings | misc
    filename: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload bytes to GCS.
    Returns the GCS object path (blob name), e.g.
      projects/abc123/rfp_templates/my_rfp.pdf
    """
    blob_name = f"projects/{project_id}/{category}/{filename}"
    blob = _bucket().blob(blob_name)
    blob.upload_from_string(file_bytes, content_type=content_type)
    return blob_name


def get_signed_url(
    blob_name: str,
    expiry_minutes: Optional[int] = None,
) -> str:
    """
    Generate a time-limited signed URL for direct browser download.
    Default expiry: GCS_SIGNED_URL_EXPIRY_MINUTES (60 min).

    Note: signed URLs require explicit credentials (not ADC).
    If using Workload Identity on Cloud Run, set GCS_CREDENTIALS_JSON instead.
    """
    minutes = expiry_minutes or _SIGNED_URL_EXPIRY_MINUTES
    blob = _bucket().blob(blob_name)
    url = blob.generate_signed_url(
        expiration=datetime.timedelta(minutes=minutes),
        method="GET",
        version="v4",
    )
    return url


def delete_file(blob_name: str) -> None:
    """Delete a file from GCS. Silent if the blob doesn't exist."""
    try:
        _bucket().blob(blob_name).delete()
    except Exception:
        pass


def list_project_files(project_id: str, category: Optional[str] = None) -> list[str]:
    """
    List all blob names under a project (optionally filtered by category).
    Returns a list of blob_name strings.
    """
    prefix = f"projects/{project_id}/"
    if category:
        prefix += f"{category}/"
    blobs = _client().list_blobs(_BUCKET_NAME, prefix=prefix)
    return [b.name for b in blobs]


def file_exists(blob_name: str) -> bool:
    """Return True if the blob exists in GCS."""
    return _bucket().blob(blob_name).exists()
