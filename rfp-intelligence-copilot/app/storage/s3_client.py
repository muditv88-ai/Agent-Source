"""
app/storage/s3_client.py  —  S3 / R2 / Local file storage abstraction

Used by:
  - app/api/routes/drawings.py  (technical drawing uploads)
  - app/api/routes/suppliers.py (onboarding document uploads)
  - app/api/routes/rfp.py       (uploaded RFP files)

Configuration (environment variables):
  STORAGE_BACKEND   : 's3' | 'r2' | 'local'  (default: 'local')
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET_NAME
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME
  LOCAL_STORAGE_PATH : absolute path for local dev storage (default: ./uploads)
"""
import io
import os
import uuid
from pathlib import Path
from typing import Optional

STORAGE_BACKEND: str = os.environ.get("STORAGE_BACKEND", "local").lower()


class StorageClient:
    """
    Unified upload / download / delete / presign interface.
    Auto-selects backend from STORAGE_BACKEND env var.
    """

    def __init__(self):
        self.backend = STORAGE_BACKEND
        self._s3 = None
        self._bucket = None
        self._local_root: Optional[Path] = None

        if self.backend in ("s3", "r2"):
            self._init_s3()
        else:
            self._init_local()

    # ── Backend initialisation ────────────────────────────────────────────────

    def _init_s3(self):
        """Initialise boto3 client for AWS S3 or Cloudflare R2."""
        try:
            import boto3  # type: ignore
        except ImportError:
            raise RuntimeError("boto3 is required for S3/R2 storage: pip install boto3")

        if self.backend == "r2":
            account_id = os.environ["R2_ACCOUNT_ID"]
            endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
            self._bucket = os.environ["R2_BUCKET_NAME"]
            self._s3 = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                region_name="auto",
            )
        else:  # AWS S3
            self._bucket = os.environ["S3_BUCKET_NAME"]
            self._s3 = boto3.client(
                "s3",
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                region_name=os.environ.get("AWS_REGION", "us-east-1"),
            )

    def _init_local(self):
        root = os.environ.get("LOCAL_STORAGE_PATH", "./uploads")
        self._local_root = Path(root)
        self._local_root.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def upload(
        self,
        file_bytes: bytes,
        key: Optional[str] = None,
        content_type: str = "application/octet-stream",
        folder: str = "uploads",
    ) -> str:
        """
        Upload file bytes and return a public (or presigned) URL.
        If key is not provided, a UUID-based key is generated.
        """
        if key is None:
            ext = content_type.split("/")[-1] if "/" in content_type else "bin"
            key = f"{folder}/{uuid.uuid4()}.{ext}"

        if self.backend in ("s3", "r2"):
            return self._upload_s3(file_bytes, key, content_type)
        return self._upload_local(file_bytes, key)

    def download(self, key: str) -> bytes:
        """Download a file by key. Returns raw bytes."""
        if self.backend in ("s3", "r2"):
            return self._download_s3(key)
        return self._download_local(key)

    def delete(self, key: str) -> bool:
        """Delete a file. Returns True on success."""
        try:
            if self.backend in ("s3", "r2"):
                self._s3.delete_object(Bucket=self._bucket, Key=key)
            else:
                path = self._local_root / key
                if path.exists():
                    path.unlink()
            return True
        except Exception:
            return False

    def presign_url(self, key: str, expires_in: int = 3600) -> str:
        """
        Generate a pre-signed URL valid for `expires_in` seconds.
        For local backend, returns the local file path (for dev use only).
        """
        if self.backend in ("s3", "r2"):
            return self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        # Local: return relative path
        return str(self._local_root / key)

    # ── S3 / R2 internals ─────────────────────────────────────────────────────

    def _upload_s3(self, file_bytes: bytes, key: str, content_type: str) -> str:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
        )
        region = os.environ.get("AWS_REGION", "us-east-1")
        if self.backend == "r2":
            account_id = os.environ.get("R2_ACCOUNT_ID", "")
            return f"https://{account_id}.r2.cloudflarestorage.com/{self._bucket}/{key}"
        return f"https://{self._bucket}.s3.{region}.amazonaws.com/{key}"

    def _download_s3(self, key: str) -> bytes:
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    # ── Local filesystem internals ─────────────────────────────────────────────

    def _upload_local(self, file_bytes: bytes, key: str) -> str:
        dest = self._local_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(file_bytes)
        return f"/static/{key}"  # served by FastAPI StaticFiles mount

    def _download_local(self, key: str) -> bytes:
        path = self._local_root / key
        if not path.exists():
            raise FileNotFoundError(f"File not found: {key}")
        return path.read_bytes()
