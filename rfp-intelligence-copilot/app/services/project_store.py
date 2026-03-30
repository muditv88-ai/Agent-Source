import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECTS_DIR = Path("projects")
PROJECTS_DIR.mkdir(exist_ok=True)


def _project_path(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def create_project(name: str) -> dict:
    project_id = str(uuid.uuid4())
    base = _project_path(project_id)
    (base / "rfp").mkdir(parents=True, exist_ok=True)
    (base / "suppliers").mkdir(exist_ok=True)
    (base / "metadata").mkdir(exist_ok=True)
    meta = {
        "project_id": project_id,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "created",
        "rfp_filename": None,
        "supplier_count": 0,
    }
    (base / "project.json").write_text(json.dumps(meta, indent=2))
    return meta


def get_project(project_id: str) -> Optional[dict]:
    path = _project_path(project_id) / "project.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    # Enrich with live file counts
    data["rfp_filename"] = _get_rfp_filename(project_id)
    data["supplier_count"] = len(get_supplier_paths(project_id))
    return data


def list_projects() -> list:
    results = []
    for p in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_dir() and (p / "project.json").exists():
            proj = get_project(p.name)
            if proj:
                results.append(proj)
    return results


def update_project_status(project_id: str, status: str) -> None:
    path = _project_path(project_id) / "project.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    data["status"] = status
    path.write_text(json.dumps(data, indent=2))


def update_project_meta(project_id: str, **kwargs) -> None:
    path = _project_path(project_id) / "project.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    data.update(kwargs)
    path.write_text(json.dumps(data, indent=2))


def delete_project(project_id: str) -> bool:
    import shutil
    base = _project_path(project_id)
    if not base.exists():
        return False
    shutil.rmtree(base)
    return True


# ── File helpers ─────────────────────────────────────────────────────────────

def get_rfp_path(project_id: str) -> Optional[Path]:
    rfp_dir = _project_path(project_id) / "rfp"
    if not rfp_dir.exists():
        return None
    files = [f for f in rfp_dir.iterdir() if f.is_file()]
    return files[0] if files else None


def _get_rfp_filename(project_id: str) -> Optional[str]:
    p = get_rfp_path(project_id)
    return p.name if p else None


def get_supplier_paths(project_id: str) -> list:
    supplier_dir = _project_path(project_id) / "suppliers"
    if not supplier_dir.exists():
        return []
    return [f for f in supplier_dir.iterdir() if f.is_file()]


def get_metadata_path(project_id: str) -> Path:
    return _project_path(project_id) / "metadata"


def get_questions_path(project_id: str) -> Path:
    return get_metadata_path(project_id) / "questions.json"


def get_suppliers_meta_path(project_id: str) -> Path:
    return get_metadata_path(project_id) / "suppliers.json"
