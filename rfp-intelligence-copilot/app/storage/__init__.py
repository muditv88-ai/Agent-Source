"""
app/storage  —  File storage abstraction layer

Provides a unified interface for storing and retrieving files.
Backend is selected by STORAGE_BACKEND env var:
  - 's3'    : AWS S3 (default in production)
  - 'r2'    : Cloudflare R2 (S3-compatible)
  - 'local' : Local filesystem (development / testing)

Usage:
    from app.storage import storage
    url = await storage.upload(file_bytes, key="drawings/abc.pdf", content_type="application/pdf")
"""
from app.storage.s3_client import StorageClient  # noqa: F401

storage = StorageClient()

__all__ = ["storage", "StorageClient"]
