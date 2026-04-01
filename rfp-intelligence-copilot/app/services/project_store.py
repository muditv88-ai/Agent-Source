"""
project_store.py  v3.3  — three-backend storage (GCS | HF Dataset | local).

Backend selection via STORAGE_BACKEND env var:

  local  (default)   Pure local filesystem. Use DATA_DIR for persistence.
                     Fine for local dev and paid HF Spaces with a volume.

  gcs                Google Cloud Storage.
                     Requires GCS_BUCKET_NAME + one of:
                       • GCS_CREDENTIALS_JSON  (recommended for HF Spaces)
                           Set this Space secret to the full contents of your
                           service-account JSON key file.
                       • GOOGLE_APPLICATION_CREDENTIALS  (path to key file)
                           Works in Docker / local dev where you mount the file.
                       • Workload Identity / ADC  (GCP-hosted environments)

  hf                 Hugging Face Dataset repo  ← recommended for HF Spaces
                     Requires HF_TOKEN + HF_REPO_ID secrets in the Space.
                     Files are written to local DATA_DIR cache AND synced
                     to the Dataset repo so they survive container restarts.

DATA_DIR env var
  Root directory for local file cache.
  Default: /data (HF Spaces / Docker) or ./data (local dev).
  Always set automatically by the Dockerfile (ENV DATA_DIR=/data).
"""
import io
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── Backend selection ──────────────────────────────────────────────────────────────

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").lower()
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "procureiq-rfp-store")

# ── GCS client ───────────────────────────────────────────────────────────────────

_gcs_client  = None
_gcs_bucket  = None
_gcs_enabled = False

if STORAGE_BACKEND == "gcs":
    try:
        from google.cloud import storage as _gcs

        _creds_json = os.environ.get("GCS_CREDENTIALS_JSON", "").strip()
        if _creds_json:
            # HF Spaces: full SA key JSON stored as a Space secret
            from google.oauth2 import service_account as _sa
            _info    = json.loads(_creds_json)
            _creds   = _sa.Credentials.from_service_account_info(
                _info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            _project = _info.get("project_id")
            _gcs_client = _gcs.Client(project=_project, credentials=_creds)
            print("[project_store] GCS auth: service-account from GCS_CREDENTIALS_JSON")
        else:
            # Docker / local dev: GOOGLE_APPLICATION_CREDENTIALS path or ADC
            _gcs_client = _gcs.Client()
            print("[project_store] GCS auth: ADC / GOOGLE_APPLICATION_CREDENTIALS")

        _gcs_bucket = _gcs_client.bucket(GCS_BUCKET_NAME)
        _gcs_bucket.reload()
        _gcs_enabled = True
        print(f"[project_store] GCS backend active: gs://{GCS_BUCKET_NAME}")
    except Exception as e:
        print(f"[project_store] GCS unavailable ({e}), falling back to local")
        STORAGE_BACKEND = "local"

# ── HF store ───────────────────────────────────────────────────────────────────

_hf = None
_hf_enabled = False

if STORAGE_BACKEND == "hf":
    from app.services import hf_store as _hf
    _hf_enabled = _hf.is_enabled()
    if not _hf_enabled:
        print("[project_store] HF backend not ready — falling back to local cache")

# ── Persistent local paths ─────────────────────────────────────────────────────

def _resolve_data_dir() -> Path:
    env_val = os.environ.get("DATA_DIR", "").strip()
    if env_val:
        return Path(env_val).resolve()
    if Path("/app/data").parent.exists():
        return Path("/app/data")
    return Path("data").resolve()


DATA_DIR: Path     = _resolve_data_dir()
PROJECTS_DIR: Path = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

print(f"[project_store] backend={STORAGE_BACKEND}  PROJECTS_DIR={PROJECTS_DIR}")

# ── Defaults ─────────────────────────────────────────────────────────────────────

_DEFAULT_FEATURE_FLAGS = {
    "chatbot_actions":     True,
    "new_analysis_engine": False,
    "pricing_scenarios":   True,
    "structured_rfp_view": False,
    "audit_logging":       True,
}
_DEFAULT_MODULE_STATES = {
    "rfp_state":       "pending",
    "technical_state": "pending",
    "pricing_state":   "pending",
}


# ════════════════════════════════════════════════════════════════════════════
# GCS helpers (unchanged from v3.1)
# ════════════════════════════════════════════════════════════════════════════

def _gcs_blob(path):
    return _gcs_bucket.blob(path)

def _gcs_write_json(path, data):
    _gcs_blob(path).upload_from_string(json.dumps(data, indent=2), content_type="application/json")

def _gcs_read_json(path):
    blob = _gcs_blob(path)
    return json.loads(blob.download_as_text()) if blob.exists() else None

def _gcs_upload_file(gcs_path, local_bytes, content_type="application/octet-stream"):
    _gcs_blob(gcs_path).upload_from_file(io.BytesIO(local_bytes), content_type=content_type)

def _gcs_download_file(gcs_path):
    blob = _gcs_blob(gcs_path)
    return blob.download_as_bytes() if blob.exists() else None

def _gcs_download_to_local(gcs_path, local_path):
    data = _gcs_download_file(gcs_path)
    if data is None:
        return False
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    return True

def _gcs_list_prefix(prefix):
    return [b.name for b in _gcs_bucket.list_blobs(prefix=prefix)]

def _gcs_delete_prefix(prefix):
    blobs = list(_gcs_bucket.list_blobs(prefix=prefix))
    if blobs:
        _gcs_bucket.delete_blobs(blobs)

def _gcs_delete_blob(path):
    blob = _gcs_blob(path)
    if blob.exists():
        blob.delete()

def _gcs_blob_metadata(gcs_path):
    blob = _gcs_blob(gcs_path)
    if not blob.exists():
        return None
    blob.reload()
    return {"size": blob.size, "updated": blob.updated.isoformat() if blob.updated else None,
            "content_type": blob.content_type}


# ════════════════════════════════════════════════════════════════════════════
# Core project CRUD
# ════════════════════════════════════════════════════════════════════════════

def _local_base(project_id):
    return PROJECTS_DIR / project_id

def _repo_meta(project_id):
    return f"projects/{project_id}/project.json"

def _repo_rfp(project_id, filename):
    return f"projects/{project_id}/rfp/{filename}"

def _repo_supplier(project_id, filename):
    return f"projects/{project_id}/suppliers/{filename}"

def _repo_meta_file(project_id, filename):
    return f"projects/{project_id}/metadata/{filename}"


def create_project(name: str, **meta_kwargs) -> dict:
    project_id = str(uuid.uuid4())
    meta = {
        "project_id":     project_id,
        "name":           name,
        "created_at":     datetime.now(timezone.utc).isoformat(),
        "status":         "created",
        "rfp_filename":   None,
        "supplier_count": 0,
        "category":       meta_kwargs.get("category"),
        "description":    meta_kwargs.get("description"),
        "stakeholders":   meta_kwargs.get("stakeholders"),
        "timeline":       meta_kwargs.get("timeline"),
        "budget":         meta_kwargs.get("budget"),
        "currency":       meta_kwargs.get("currency"),
        "module_states":  dict(_DEFAULT_MODULE_STATES),
    }
    if _gcs_enabled:
        _gcs_write_json(_repo_meta(project_id), meta)
    elif STORAGE_BACKEND == "hf":
        _hf.write_json(_repo_meta(project_id), meta)
    else:
        base = _local_base(project_id)
        (base / "rfp").mkdir(parents=True, exist_ok=True)
        (base / "suppliers").mkdir(exist_ok=True)
        (base / "metadata").mkdir(exist_ok=True)
        (base / "project.json").write_text(json.dumps(meta, indent=2))
    return meta


def get_project(project_id: str) -> Optional[dict]:
    if _gcs_enabled:
        data = _gcs_read_json(_repo_meta(project_id))
        if not data:
            return None
        rfp_blobs = _gcs_list_prefix(f"projects/{project_id}/rfp/")
        rfp_files = [b.split("/")[-1] for b in rfp_blobs if not b.endswith("/")]
        data["rfp_filename"] = rfp_files[0] if rfp_files else None
        sup_blobs = _gcs_list_prefix(f"projects/{project_id}/suppliers/")
        data["supplier_count"] = len([b for b in sup_blobs if not b.endswith("/")])
    elif STORAGE_BACKEND == "hf":
        data = _hf.read_json(_repo_meta(project_id))
        if not data:
            return None
        rfp_files = [p.split("/")[-1] for p in _hf.list_prefix(f"projects/{project_id}/rfp")]
        data["rfp_filename"] = rfp_files[0] if rfp_files else None
        data["supplier_count"] = len(_hf.list_prefix(f"projects/{project_id}/suppliers"))
    else:
        path = _local_base(project_id) / "project.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        data["rfp_filename"] = _get_rfp_filename_local(project_id)
        data["supplier_count"] = len(get_supplier_paths(project_id))
    data.setdefault("module_states", dict(_DEFAULT_MODULE_STATES))
    return data


def list_projects() -> list:
    if _gcs_enabled:
        blobs = _gcs_list_prefix("projects/")
        pids = {b.split("/")[1] for b in blobs if len(b.split("/")) >= 3 and b.split("/")[2] == "project.json"}
    elif STORAGE_BACKEND == "hf":
        paths = _hf.list_prefix("projects")
        pids = {p.split("/")[1] for p in paths if len(p.split("/")) >= 3 and p.split("/")[2] == "project.json"}
    else:
        pids = [p.name for p in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
                if p.is_dir() and (p / "project.json").exists()] if PROJECTS_DIR.exists() else []
    results = [proj for pid in pids if (proj := get_project(pid))]
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return results


def update_project_meta(project_id: str, **kwargs) -> None:
    if _gcs_enabled:
        data = _gcs_read_json(_repo_meta(project_id)) or {}
        data.update(kwargs)
        _gcs_write_json(_repo_meta(project_id), data)
    elif STORAGE_BACKEND == "hf":
        data = _hf.read_json(_repo_meta(project_id)) or {}
        data.update(kwargs)
        _hf.write_json(_repo_meta(project_id), data)
    else:
        path = _local_base(project_id) / "project.json"
        if not path.exists():
            return
        data = json.loads(path.read_text())
        data.update(kwargs)
        path.write_text(json.dumps(data, indent=2))


def update_project_status(project_id: str, status: str) -> None:
    update_project_meta(project_id, status=status)


def delete_project(project_id: str) -> bool:
    if _gcs_enabled:
        _gcs_delete_prefix(f"projects/{project_id}/")
    elif STORAGE_BACKEND == "hf":
        _hf.delete_prefix(f"projects/{project_id}")
    else:
        import shutil
        base = _local_base(project_id)
        if not base.exists():
            return False
        shutil.rmtree(base)
    return True


# ════════════════════════════════════════════════════════════════════════════
# File upload / download
# ════════════════════════════════════════════════════════════════════════════

def save_rfp_file(project_id: str, filename: str, data: bytes) -> Path:
    local_path = PROJECTS_DIR / project_id / "rfp" / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    if _gcs_enabled:
        _gcs_upload_file(f"projects/{project_id}/rfp/{filename}", data)
    elif STORAGE_BACKEND == "hf":
        _hf.write_bytes(_repo_rfp(project_id, filename), data)
    return local_path


def save_supplier_file(project_id: str, filename: str, data: bytes) -> Path:
    local_path = PROJECTS_DIR / project_id / "suppliers" / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    if _gcs_enabled:
        _gcs_upload_file(f"projects/{project_id}/suppliers/{filename}", data)
    elif STORAGE_BACKEND == "hf":
        _hf.write_bytes(_repo_supplier(project_id, filename), data)
    return local_path


def save_metadata(project_id: str, filename: str, data) -> Path:
    local_path = PROJECTS_DIR / project_id / "metadata" / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(json.dumps(data, indent=2))
    if _gcs_enabled:
        if isinstance(data, (dict, list)):
            _gcs_write_json(f"projects/{project_id}/metadata/{filename}", data)
        else:
            _gcs_upload_file(f"projects/{project_id}/metadata/{filename}",
                             json.dumps(data, indent=2).encode(), content_type="application/json")
    elif STORAGE_BACKEND == "hf":
        _hf.write_json(_repo_meta_file(project_id, filename), data)
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
    if STORAGE_BACKEND == "hf":
        return _hf.read_json(_repo_meta_file(project_id, filename))
    return None


def ensure_rfp_local(project_id: str) -> Optional[Path]:
    local = get_rfp_path(project_id)
    if local and local.exists():
        return local
    if _gcs_enabled:
        blobs = [b for b in _gcs_list_prefix(f"projects/{project_id}/rfp/") if not b.endswith("/")]
        if not blobs:
            return None
        gcs_path   = blobs[0]
        filename   = gcs_path.split("/")[-1]
        local_path = PROJECTS_DIR / project_id / "rfp" / filename
        _gcs_download_to_local(gcs_path, local_path)
        return local_path
    if STORAGE_BACKEND == "hf":
        paths = _hf.list_prefix(f"projects/{project_id}/rfp")
        if not paths:
            return None
        repo_path  = paths[0]
        filename   = repo_path.split("/")[-1]
        local_path = PROJECTS_DIR / project_id / "rfp" / filename
        data = _hf.read_bytes(repo_path)
        if data:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(data)
            return local_path
    return None


def ensure_suppliers_local(project_id: str) -> list[Path]:
    local_paths = get_supplier_paths(project_id)
    if local_paths:
        return local_paths
    if _gcs_enabled:
        blobs = [b for b in _gcs_list_prefix(f"projects/{project_id}/suppliers/") if not b.endswith("/")]
        result = []
        for gcs_path in blobs:
            filename   = gcs_path.split("/")[-1]
            local_path = PROJECTS_DIR / project_id / "suppliers" / filename
            if not local_path.exists():
                _gcs_download_to_local(gcs_path, local_path)
            result.append(local_path)
        return result
    if STORAGE_BACKEND == "hf":
        paths = _hf.list_prefix(f"projects/{project_id}/suppliers")
        result = []
        for repo_path in paths:
            filename   = repo_path.split("/")[-1]
            local_path = PROJECTS_DIR / project_id / "suppliers" / filename
            if not local_path.exists():
                data = _hf.read_bytes(repo_path)
                if data:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(data)
            result.append(local_path)
        return result
    return []


def delete_supplier_file(project_id: str, filename: str) -> bool:
    local_path = PROJECTS_DIR / project_id / "suppliers" / filename
    if local_path.exists():
        local_path.unlink()
    if _gcs_enabled:
        _gcs_delete_blob(f"projects/{project_id}/suppliers/{filename}")
    elif STORAGE_BACKEND == "hf":
        _hf.delete_file(_repo_supplier(project_id, filename))
    return True


def delete_rfp_file(project_id: str) -> bool:
    local = get_rfp_path(project_id)
    if local and local.exists():
        local.unlink()
    if _gcs_enabled:
        for b in _gcs_list_prefix(f"projects/{project_id}/rfp/"):
            if not b.endswith("/"):
                _gcs_delete_blob(b)
    elif STORAGE_BACKEND == "hf":
        for rp in _hf.list_prefix(f"projects/{project_id}/rfp"):
            _hf.delete_file(rp)
    return True


# ════════════════════════════════════════════════════════════════════════════
# File listing
# ════════════════════════════════════════════════════════════════════════════

def list_project_files(project_id: str) -> dict:
    supplier_meta  = load_metadata(project_id, "suppliers.json") or {}
    display_names  = {Path(k).name: v for k, v in supplier_meta.items()}

    if _gcs_enabled:
        rfp_blobs = [b for b in _gcs_list_prefix(f"projects/{project_id}/rfp/") if not b.endswith("/")]
        sup_blobs = [b for b in _gcs_list_prefix(f"projects/{project_id}/suppliers/") if not b.endswith("/")]
        rfp_files = [{"filename": b.split("/")[-1], **(_gcs_blob_metadata(b) or {}), "storage": "gcs"} for b in rfp_blobs]
        sup_files = [{"filename": b.split("/")[-1], "display_name": display_names.get(b.split("/")[-1], b.split("/")[-1]),
                      **(_gcs_blob_metadata(b) or {}), "storage": "gcs"} for b in sup_blobs]
        return {"rfp": rfp_files, "suppliers": sup_files, "storage_backend": "gcs"}

    elif STORAGE_BACKEND == "hf":
        rfp_paths = _hf.list_prefix(f"projects/{project_id}/rfp")
        sup_paths = _hf.list_prefix(f"projects/{project_id}/suppliers")
        rfp_files = [{"filename": p.split("/")[-1], "storage": "hf"} for p in rfp_paths]
        sup_files = [{"filename": p.split("/")[-1],
                      "display_name": display_names.get(p.split("/")[-1], p.split("/")[-1]),
                      "storage": "hf"} for p in sup_paths]
        return {"rfp": rfp_files, "suppliers": sup_files, "storage_backend": "hf"}

    else:
        rfp_dir = PROJECTS_DIR / project_id / "rfp"
        sup_dir = PROJECTS_DIR / project_id / "suppliers"
        rfp_files, sup_files = [], []
        if rfp_dir.exists():
            for f in rfp_dir.iterdir():
                if f.is_file():
                    st = f.stat()
                    rfp_files.append({"filename": f.name, "size": st.st_size,
                                      "uploaded_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                                      "storage": "local"})
        if sup_dir.exists():
            for f in sup_dir.iterdir():
                if f.is_file():
                    st = f.stat()
                    sup_files.append({"filename": f.name, "display_name": display_names.get(f.name, f.name),
                                      "size": st.st_size,
                                      "uploaded_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                                      "storage": "local"})
        return {"rfp": rfp_files, "suppliers": sup_files, "storage_backend": "local"}


def get_signed_url(project_id, role, filename, expiry_minutes=60):
    if role not in ("rfp", "suppliers"):
        role = "suppliers"
    gcs_path = f"projects/{project_id}/{role}/{filename}"
    if _gcs_enabled:
        blob = _gcs_blob(gcs_path)
        if not blob.exists():
            return None
        return blob.generate_signed_url(expiration=timedelta(minutes=expiry_minutes), method="GET", version="v4")
    return None


# ════════════════════════════════════════════════════════════════════════════
# Path helpers
# ════════════════════════════════════════════════════════════════════════════

def get_rfp_path(project_id):
    rfp_dir = PROJECTS_DIR / project_id / "rfp"
    if not rfp_dir.exists():
        return None
    files = [f for f in rfp_dir.iterdir() if f.is_file()]
    return files[0] if files else None

def _get_rfp_filename_local(project_id):
    p = get_rfp_path(project_id)
    return p.name if p else None

def get_supplier_paths(project_id):
    sup_dir = PROJECTS_DIR / project_id / "suppliers"
    if not sup_dir.exists():
        return []
    return [f for f in sup_dir.iterdir() if f.is_file()]

def get_questions_path(project_id):
    return PROJECTS_DIR / project_id / "metadata" / "questions.json"

def get_suppliers_meta_path(project_id):
    return PROJECTS_DIR / project_id / "metadata" / "suppliers.json"

def is_gcs_enabled():
    return _gcs_enabled

def is_hf_enabled():
    return STORAGE_BACKEND == "hf" and _hf_enabled


# ════════════════════════════════════════════════════════════════════════════
# Module states
# ════════════════════════════════════════════════════════════════════════════

def get_module_states(project_id):
    project = get_project(project_id)
    if not project:
        return dict(_DEFAULT_MODULE_STATES)
    return project.get("module_states", dict(_DEFAULT_MODULE_STATES))

def update_module_state(project_id, module, state):
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
# Feature flags
# ════════════════════════════════════════════════════════════════════════════

def get_feature_flags(project_id):
    stored = load_metadata(project_id, "feature_flags.json")
    if stored is None:
        return dict(_DEFAULT_FEATURE_FLAGS)
    return {**_DEFAULT_FEATURE_FLAGS, **stored}

def set_feature_flags(project_id, updates):
    current = get_feature_flags(project_id)
    for key, val in updates.items():
        if key in _DEFAULT_FEATURE_FLAGS:
            current[key] = bool(val)
    save_metadata(project_id, "feature_flags.json", current)
    return current


# ════════════════════════════════════════════════════════════════════════════
# Audit log
# ════════════════════════════════════════════════════════════════════════════

def save_audit_log(project_id, entry):
    existing = load_metadata(project_id, "audit_log.json") or []
    if not isinstance(existing, list):
        existing = []
    existing.append(entry)
    if len(existing) > 500:
        existing = existing[-500:]
    save_metadata(project_id, "audit_log.json", existing)

def load_audit_log(project_id, limit=50):
    log = load_metadata(project_id, "audit_log.json") or []
    return log[-limit:] if isinstance(log, list) else []
