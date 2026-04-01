"""
gcs_storage.py

Google Cloud Storage helper for persistent project file storage.

Setup:
  1. Create a GCS bucket (e.g. rfp-copilot-files).
  2. Grant the service account the role  roles/storage.objectAdmin.
  3. Set env vars:
       GCS_BUCKET_NAME       = rfp-copilot-files
       GOOGLE_APPLICATION_CREDENTIALS = /path/to/service-account.json
         (or use Workload Identity on GCE/GKE)

File layout inside the bucket:
  projects/<project_id>/rfp_templates/<filename>
  projects/<project_id>/supplier_responses/<filename>
  projects/<project_id>/drawings/<filename>
  projects/<project_id>/misc/<filename>
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Optional

try:
    from google.cloud import storage as gcs
    _GCS_AVAILABLE = True
except ImportError:
    _GCS_AVAILABLE = False

_BUCKET_NAME: str = os.getenv("GCS_BUCKET_NAME", "rfp-copilot-files")
_SIGNED_URL_EXPIRY_MINUTES: int = int(os.getenv("GCS_SIGNED_URL_EXPIRY_MINUTES", "60"))


def _client():
    """Return a GCS storage client (raises if library not installed)."""
    if not _GCS_AVAILABLE:
        raise RuntimeError(
            "google-cloud-storage is not installed. "
            "Run: pip install google-cloud-storage"
        )
    return gcs.Client()


def _bucket():
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
    """Delete a file from GCS. Silent if it doesn't exist."""
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
