"""
hf_store.py  —  Hugging Face Dataset repo persistence backend.

When STORAGE_BACKEND=hf, project_store delegates all reads and writes
through this module instead of the local filesystem.

How it works
------------
A private HF Dataset repo (configured via HF_REPO_ID) acts as the
persistent store. The repo layout mirrors the local filesystem layout:

  projects/{project_id}/project.json
  projects/{project_id}/rfp/{filename}
  projects/{project_id}/suppliers/{filename}
  projects/{project_id}/metadata/{filename}

On every write the file is:
  1. Written to DATA_DIR (local cache for fast reads within the same
     container lifetime)
  2. Uploaded to the HF Dataset repo via huggingface_hub

On every read the file is:
  1. Served from local DATA_DIR cache if present
  2. Downloaded from HF Dataset repo and cached locally if not

This means:
  - First request after a cold start (fresh container) hits HF once per
    file, then is served from the local cache for the rest of the session.
  - Writes are synchronous (upload happens inline). For high-throughput
    production use, swap to an async background upload queue.

Required environment variables (set as HF Space Secrets)
---------------------------------------------------------
  HF_TOKEN    — A Hugging Face token with WRITE access to the repo.
                 Generate at https://huggingface.co/settings/tokens
  HF_REPO_ID  — The Dataset repo to use, e.g. "myorg/sourceiq-data".
                 Create a *private* Dataset repo first.

Optional
--------
  DATA_DIR    — Local cache root (default /data on HF Spaces).
                 Set automatically by the Dockerfile.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

HF_TOKEN   = os.environ.get("HF_TOKEN", "")
HF_REPO_ID = os.environ.get("HF_REPO_ID", "")

_DATA_DIR_ENV = os.environ.get("DATA_DIR", "").strip()
if _DATA_DIR_ENV:
    CACHE_DIR = Path(_DATA_DIR_ENV).resolve()
elif Path("/data").exists():
    CACHE_DIR = Path("/data")
else:
    CACHE_DIR = Path("data").resolve()

_hf_api = None
_hf_enabled = False

if HF_TOKEN and HF_REPO_ID:
    try:
        from huggingface_hub import HfApi
        _hf_api = HfApi(token=HF_TOKEN)
        # Verify access by fetching repo info
        _hf_api.repo_info(repo_id=HF_REPO_ID, repo_type="dataset")
        _hf_enabled = True
        print(f"[hf_store] HF Dataset backend active: {HF_REPO_ID}")
    except Exception as e:
        print(f"[hf_store] HF Dataset backend unavailable ({e}). Falling back to local cache only.")
else:
    missing = [v for v in ("HF_TOKEN", "HF_REPO_ID") if not os.environ.get(v)]
    print(f"[hf_store] HF backend disabled — missing env vars: {', '.join(missing)}")


def is_enabled() -> bool:
    return _hf_enabled


# ── Internal helpers ────────────────────────────────────────────────────────────

def _cache_path(repo_path: str) -> Path:
    """Return the local cache path for a given repo-relative path."""
    p = CACHE_DIR / repo_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _upload(repo_path: str, local_path: Path) -> None:
    """Upload a local file to the HF Dataset repo."""
    if not _hf_enabled:
        return
    try:
        _hf_api.upload_file(
            path_or_fileobj=str(local_path),
            path_in_repo=repo_path,
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            commit_message=f"update {repo_path}",
        )
    except Exception as e:
        print(f"[hf_store] Upload failed for {repo_path}: {e}")


def _download(repo_path: str, local_path: Path) -> bool:
    """Download a file from HF Dataset repo to local cache. Returns True on success."""
    if not _hf_enabled:
        return False
    try:
        from huggingface_hub import hf_hub_download
        downloaded = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=repo_path,
            repo_type="dataset",
            token=HF_TOKEN,
            local_dir=str(CACHE_DIR),
        )
        # hf_hub_download places file under CACHE_DIR/repo_path automatically
        src = Path(downloaded)
        if src.resolve() != local_path.resolve() and src.exists():
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(src.read_bytes())
        return local_path.exists()
    except Exception as e:
        print(f"[hf_store] Download failed for {repo_path}: {e}")
        return False


def _list_repo_prefix(prefix: str) -> list[str]:
    """List all file paths in the repo under a given prefix."""
    if not _hf_enabled:
        return []
    try:
        from huggingface_hub import list_repo_tree
        items = list_repo_tree(
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            path_in_repo=prefix,
            token=HF_TOKEN,
            recursive=True,
        )
        return [item.path for item in items if hasattr(item, "path") and not item.path.endswith("/")]
    except Exception as e:
        print(f"[hf_store] list_repo_tree failed for prefix '{prefix}': {e}")
        return []


def _delete_repo_file(repo_path: str) -> None:
    """Delete a single file from the HF Dataset repo."""
    if not _hf_enabled:
        return
    try:
        _hf_api.delete_file(
            path_in_repo=repo_path,
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            commit_message=f"delete {repo_path}",
        )
    except Exception as e:
        print(f"[hf_store] Delete failed for {repo_path}: {e}")


# ── Public API (called by project_store when STORAGE_BACKEND=hf) ───────────────

def write_json(repo_path: str, data: dict | list) -> Path:
    """
    Write a dict/list as JSON to both local cache and HF repo.
    Returns the local cache Path.
    """
    local = _cache_path(repo_path)
    local.write_text(json.dumps(data, indent=2))
    _upload(repo_path, local)
    return local


def read_json(repo_path: str) -> Optional[dict | list]:
    """
    Read JSON from local cache; download from HF repo if not cached.
    Returns None if not found anywhere.
    """
    local = _cache_path(repo_path)
    if local.exists():
        try:
            return json.loads(local.read_text())
        except Exception:
            pass
    if _download(repo_path, local):
        try:
            return json.loads(local.read_text())
        except Exception:
            pass
    return None


def write_bytes(repo_path: str, data: bytes) -> Path:
    """
    Write raw bytes to both local cache and HF repo.
    Returns the local cache Path.
    """
    local = _cache_path(repo_path)
    local.write_bytes(data)
    _upload(repo_path, local)
    return local


def read_bytes(repo_path: str) -> Optional[bytes]:
    """
    Read bytes from local cache; download from HF repo if not cached.
    Returns None if not found.
    """
    local = _cache_path(repo_path)
    if local.exists():
        return local.read_bytes()
    if _download(repo_path, local):
        return local.read_bytes()
    return None


def list_prefix(prefix: str) -> list[str]:
    """
    List all repo paths under a prefix.
    Falls back to scanning local cache if HF is unreachable.
    """
    if _hf_enabled:
        remote = _list_repo_prefix(prefix)
        if remote:
            return remote
    # Local cache fallback
    local_base = CACHE_DIR / prefix
    if not local_base.exists():
        return []
    return [
        str((CACHE_DIR / prefix / f).relative_to(CACHE_DIR)).replace("\\", "/")
        for f in local_base.rglob("*")
        if f.is_file()
    ]


def delete_file(repo_path: str) -> None:
    """Delete a file from both local cache and HF repo."""
    local = _cache_path(repo_path)
    if local.exists():
        local.unlink()
    _delete_repo_file(repo_path)


def delete_prefix(prefix: str) -> None:
    """Delete all files under a prefix from both local cache and HF repo."""
    for rpath in list_prefix(prefix):
        delete_file(rpath)
