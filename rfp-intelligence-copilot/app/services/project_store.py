"""
project_store.py — dual-backend storage (GCS or local filesystem).

Set STORAGE_BACKEND=gcs to use Google Cloud Storage.
Falls back to local filesystem if GCS is unavailable or STORAGE_BACKEND!=gcs.

GCS layout:
  projects/{project_id}/project.json
  projects/{project_id}/rfp/{filename}
  projects/{project_id}/suppliers/{filename}
  projects/{project_id}/metadata/questions.json
  projects/{project_id}/metadata/suppliers.json
  projects/{project_id}/metadata/feature_flags.json   [NEW v2.0]
  projects/{project_id}/metadata/audit_log.json       [NEW v2.0]

v2.0 additions (all backward-compatible):
  - create_project() accepts optional meta kwargs
  - update_module_state()  / get_module_states()
  - save_audit_log()       / load_audit_log()
  - get_feature_flags()    / set_feature_flags()
"""
import io
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Backend selection ────────────────────────────────────────────────────────

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").lower()
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "procureiq-rfp-store")

_gcs_client  = None
_gcs_bucket  = None
_gcs_enabled = False

if STORAGE_BACKEND == "gcs":
    try:
        from google.cloud import storage as _gcs
        _gcs_client  = _gcs.Client()
        _gcs_bucket  = _gcs_client.bucket(GCS_BUCKET_NAME)
        _gcs_bucket.reload()
        _gcs_enabled = True
        print(f"[project_store] GCS backend active: gs://{GCS_BUCKET_NAME}")
    except Exception as e:
        print(f"[project_store] GCS unavailable ({e}), falling back to local storage")

if not _gcs_enabled:
    print("[project_store] Using local filesystem storage")

# ── Local paths ──────────────────────────────────────────────────────────────

PROJECTS_DIR = Path("projects")
PROJECTS_DIR.mkdir(exist_ok=True)

# ── Default feature flags ─────────────────────────────────────────────────────
_DEFAULT_FEATURE_FLAGS = {
    "chatbot_actions":     True,
    "new_analysis_engine": False,
    "pricing_scenarios":   True,
    "structured_rfp_view": False,
    "audit_logging":       True,
}

# ── Default module states ─────────────────────────────────────────────────────
_DEFAULT_MODULE_STATES = {
    "rfp_state":       "pending",
    "technical_state": "pending",
    "pricing_state":   "pending",
}


# ════════════════════════════════════════════════════════════════════════════
# GCS helpers (UNCHANGED from v1)
# ════════════════════════════════════════════════════════════════════════════

def _gcs_blob(path: str):
    return _gcs_bucket.blob(path)

def _gcs_write_json(path: str, data: dict):
    blob = _gcs_blob(path)
    blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")

def _gcs_read_json(path: str) -> Optional[dict]:
    blob = _gcs_blob(path)
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())

def _gcs_upload_file(gcs_path: str, local_bytes: bytes, content_type: str = "application/octet-stream"):
    blob = _gcs_blob(gcs_path)
    blob.upload_from_file(io.BytesIO(local_bytes), content_type=content_type)

def _gcs_download_file(gcs_path: str) -> Optional[bytes]:
    blob = _gcs_blob(gcs_path)
    if not blob.exists():
        return None
    return blob.download_as_bytes()

def _gcs_download_to_local(gcs_path: str, local_path: Path) -> bool:
    data = _gcs_download_file(gcs_path)
    if data is None:
        return False
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    return True

def _gcs_list_prefix(prefix: str) -> list[str]:
    return [b.name for b in _gcs_bucket.list_blobs(prefix=prefix)]

def _gcs_delete_prefix(prefix: str):
    blobs = list(_gcs_bucket.list_blobs(prefix=prefix))
    if blobs:
        _gcs_bucket.delete_blobs(blobs)

def _gcs_delete_blob(path: str):
    blob = _gcs_blob(path)
    if blob.exists():
        blob.delete()


# ════════════════════════════════════════════════════════════════════════════
# Core project CRUD  (EXISTING — only create_project extended with **meta_kwargs)
# ════════════════════════════════════════════════════════════════════════════

def create_project(name: str, **meta_kwargs) -> dict:
    """
    Create a new project.
    v2.0: accepts optional keyword args: category, description,
          stakeholders, timeline, budget, currency.
    All callers that pass only `name` continue to work unchanged.
    """
    project_id = str(uuid.uuid4())
    meta = {
        "project_id":    project_id,
        "name":          name,
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "status":        "created",
        "rfp_filename":  None,
        "supplier_count": 0,
        # v2.0 optional fields — all None by default
        "category":      meta_kwargs.get("category"),
        "description":   meta_kwargs.get("description"),
        "stakeholders":  meta_kwargs.get("stakeholders"),
        "timeline":      meta_kwargs.get("timeline"),
        "budget":        meta_kwargs.get("budget"),
        "currency":      meta_kwargs.get("currency"),
        # v2.0 module states — defaults allow existing status field to coexist
        "module_states": dict(_DEFAULT_MODULE_STATES),
    }
    if _gcs_enabled:
        _gcs_write_json(f"projects/{project_id}/project.json", meta)
    else:
        base = PROJECTS_DIR / project_id
        (base / "rfp").mkdir(parents=True, exist_ok=True)
        (base / "suppliers").mkdir(exist_ok=True)
        (base / "metadata").mkdir(exist_ok=True)
        (base / "project.json").write_text(json.dumps(meta, indent=2))
    return meta


def get_project(project_id: str) -> Optional[dict]:
    """UNCHANGED from v1 — returns project dict with rfp_filename + supplier_count."""
    if _gcs_enabled:
        data = _gcs_read_json(f"projects/{project_id}/project.json")
        if not data:
            return None
        rfp_blobs = _gcs_list_prefix(f"projects/{project_id}/rfp/")
        rfp_files = [b.split("/")[-1] for b in rfp_blobs if not b.endswith("/")]
        data["rfp_filename"] = rfp_files[0] if rfp_files else None
        sup_blobs = _gcs_list_prefix(f"projects/{project_id}/suppliers/")
        data["supplier_count"] = len([b for b in sup_blobs if not b.endswith("/")])
        # Backfill module_states for existing projects that pre-date v2.0
        data.setdefault("module_states", dict(_DEFAULT_MODULE_STATES))
        return data
    else:
        path = PROJECTS_DIR / project_id / "project.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        data["rfp_filename"] = _get_rfp_filename_local(project_id)
        data["supplier_count"] = len(get_supplier_paths(project_id))
        data.setdefault("module_states", dict(_DEFAULT_MODULE_STATES))
        return data


def list_projects() -> list:
    """UNCHANGED from v1."""
    if _gcs_enabled:
        blobs = _gcs_list_prefix("projects/")
        project_ids = set()
        for b in blobs:
            parts = b.split("/")
            if len(parts) >= 3 and parts[2] == "project.json":
                project_ids.add(parts[1])
        results = []
        for pid in project_ids:
            proj = get_project(pid)
            if proj:
                results.append(proj)
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results
    else:
        results = []
        if not PROJECTS_DIR.exists():
            return results
        for p in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_dir() and (p / "project.json").exists():
                proj = get_project(p.name)
                if proj:
                    results.append(proj)
        return results


def update_project_meta(project_id: str, **kwargs) -> None:
    """UNCHANGED from v1 — already accepts arbitrary kwargs."""
    if _gcs_enabled:
        data = _gcs_read_json(f"projects/{project_id}/project.json") or {}
        data.update(kwargs)
        _gcs_write_json(f"projects/{project_id}/project.json", data)
    else:
        path = PROJECTS_DIR / project_id / "project.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        data.update(kwargs)
        path.write_text(json.dumps(data, indent=2))


def update_project_status(project_id: str, status: str) -> None:
    """UNCHANGED from v1."""
    update_project_meta(project_id, status=status)


def delete_project(project_id: str) -> bool:
    """UNCHANGED from v1."""
    if _gcs_enabled:
        _gcs_delete_prefix(f"projects/{project_id}/")
        return True
    else:
        import shutil
        base = PROJECTS_DIR / project_id
        if not base.exists():
            return False
        shutil.rmtree(base)
        return True


# ════════════════════════════════════════════════════════════════════════════
# File upload / download (UNCHANGED from v1)
# ════════════════════════════════════════════════════════════════════════════

def save_rfp_file(project_id: str, filename: str, data: bytes) -> Path:
    local_path = PROJECTS_DIR / project_id / "rfp" / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    if _gcs_enabled:
        _gcs_upload_file(f"projects/{project_id}/rfp/{filename}", data)
    return local_path

def save_supplier_file(project_id: str, filename: str, data: bytes) -> Path:
    local_path = PROJECTS_DIR / project_id / "suppliers" / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    if _gcs_enabled:
        _gcs_upload_file(f"projects/{project_id}/suppliers/{filename}", data)
    return local_path

def save_metadata(project_id: str, filename: str, data: dict) -> Path:
    local_path = PROJECTS_DIR / project_id / "metadata" / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(json.dumps(data, indent=2))
    if _gcs_enabled:
        _gcs_write_json(f"projects/{project_id}/metadata/{filename}", data)
    return local_path

def load_metadata(project_id: str, filename: str) -> Optional[dict]:
    local_path = PROJECTS_DIR / project_id / "metadata" / filename
    if local_path.exists():
        return json.loads(local_path.read_text())
    if _gcs_enabled:
        data = _gcs_read_json(f"projects/{project_id}/metadata/{filename}")
        if data:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(json.dumps(data, indent=2))
        return data
    return None

def ensure_rfp_local(project_id: str) -> Optional[Path]:
    local = get_rfp_path(project_id)
    if local and local.exists():
        return local
    if not _gcs_enabled:
        return None
    blobs = _gcs_list_prefix(f"projects/{project_id}/rfp/")
    rfp_blobs = [b for b in blobs if not b.endswith("/")]
    if not rfp_blobs:
        return None
    gcs_path  = rfp_blobs[0]
    filename  = gcs_path.split("/")[-1]
    local_path = PROJECTS_DIR / project_id / "rfp" / filename
    _gcs_download_to_local(gcs_path, local_path)
    return local_path

def ensure_suppliers_local(project_id: str) -> list[Path]:
    local_paths = get_supplier_paths(project_id)
    if local_paths:
        return local_paths
    if not _gcs_enabled:
        return []
    blobs = _gcs_list_prefix(f"projects/{project_id}/suppliers/")
    sup_blobs = [b for b in blobs if not b.endswith("/")]
    result = []
    for gcs_path in sup_blobs:
        filename   = gcs_path.split("/")[-1]
        local_path = PROJECTS_DIR / project_id / "suppliers" / filename
        if not local_path.exists():
            _gcs_download_to_local(gcs_path, local_path)
        result.append(local_path)
    return result

def delete_supplier_file(project_id: str, filename: str) -> bool:
    local_path = PROJECTS_DIR / project_id / "suppliers" / filename
    deleted = False
    if local_path.exists():
        local_path.unlink()
        deleted = True
    if _gcs_enabled:
        _gcs_delete_blob(f"projects/{project_id}/suppliers/{filename}")
        deleted = True
    return deleted


# ════════════════════════════════════════════════════════════════════════════
# Path helpers (UNCHANGED from v1)
# ════════════════════════════════════════════════════════════════════════════

def get_rfp_path(project_id: str) -> Optional[Path]:
    rfp_dir = PROJECTS_DIR / project_id / "rfp"
    if not rfp_dir.exists():
        return None
    files = [f for f in rfp_dir.iterdir() if f.is_file()]
    return files[0] if files else None

def _get_rfp_filename_local(project_id: str) -> Optional[str]:
    p = get_rfp_path(project_id)
    return p.name if p else None

def get_supplier_paths(project_id: str) -> list[Path]:
    sup_dir = PROJECTS_DIR / project_id / "suppliers"
    if not sup_dir.exists():
        return []
    return [f for f in sup_dir.iterdir() if f.is_file()]

def get_questions_path(project_id: str) -> Path:
    return PROJECTS_DIR / project_id / "metadata" / "questions.json"

def get_suppliers_meta_path(project_id: str) -> Path:
    return PROJECTS_DIR / project_id / "metadata" / "suppliers.json"

def is_gcs_enabled() -> bool:
    return _gcs_enabled


# ════════════════════════════════════════════════════════════════════════════
# NEW v2.0 — Module state management
# ════════════════════════════════════════════════════════════════════════════

def get_module_states(project_id: str) -> dict:
    """
    Return module_states dict for a project.
    Backfills defaults for projects created before v2.0.
    """
    project = get_project(project_id)
    if not project:
        return dict(_DEFAULT_MODULE_STATES)
    return project.get("module_states", dict(_DEFAULT_MODULE_STATES))


def update_module_state(project_id: str, module: str, state: str) -> dict:
    """
    Set state for one module.  module = 'rfp' | 'technical' | 'pricing'
    state = 'pending' | 'active' | 'complete' | 'error'
    Returns updated module_states dict.
    """
    valid_modules = {"rfp", "technical", "pricing"}
    valid_states  = {"pending", "active", "complete", "error"}
    if module not in valid_modules:
        raise ValueError(f"Invalid module '{module}'. Must be one of {valid_modules}")
    if state not in valid_states:
        raise ValueError(f"Invalid state '{state}'. Must be one of {valid_states}")

    current = get_module_states(project_id)
    current[f"{module}_state"] = state
    update_project_meta(project_id, module_states=current)
    return current


# ════════════════════════════════════════════════════════════════════════════
# NEW v2.0 — Feature flags
# ════════════════════════════════════════════════════════════════════════════

def get_feature_flags(project_id: str) -> dict:
    """
    Load feature flags for project. Returns defaults if not yet set.
    Safe to call on any project, including those pre-dating v2.0.
    """
    stored = load_metadata(project_id, "feature_flags.json")
    if stored is None:
        return dict(_DEFAULT_FEATURE_FLAGS)
    # Merge with defaults so new flags added in future releases have values
    merged = dict(_DEFAULT_FEATURE_FLAGS)
    merged.update(stored)
    return merged


def set_feature_flags(project_id: str, updates: dict) -> dict:
    """
    Merge updates into existing feature flags and persist.
    Only keys present in updates are changed.
    """
    current = get_feature_flags(project_id)
    # Only allow known flag keys to prevent arbitrary writes
    for key, val in updates.items():
        if key in _DEFAULT_FEATURE_FLAGS:
            current[key] = bool(val)
    save_metadata(project_id, "feature_flags.json", current)
    return current


# ════════════════════════════════════════════════════════════════════════════
# NEW v2.0 — Audit log
# ════════════════════════════════════════════════════════════════════════════

def save_audit_log(project_id: str, entry: dict) -> None:
    """
    Append a single audit entry to audit_log.json.
    Creates the file if it does not exist.
    Thread-safety note: for high concurrency, use a queue; for current
    single-worker deployment, file append is safe.
    """
    existing = load_metadata(project_id, "audit_log.json") or []
    if not isinstance(existing, list):
        existing = []
    existing.append(entry)
    # Keep last 500 entries to prevent unbounded growth
    if len(existing) > 500:
        existing = existing[-500:]
    save_metadata(project_id, "audit_log.json", existing)


def load_audit_log(project_id: str, limit: int = 50) -> list:
    """
    Return the most recent `limit` audit entries for a project.
    Returns [] if no log exists yet.
    """
    log = load_metadata(project_id, "audit_log.json") or []
    if not isinstance(log, list):
        return []
    return log[-limit:]
