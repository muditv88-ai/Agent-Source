"""In-memory job store for async analysis jobs."""
import uuid
from typing import Dict, Any, Optional
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def create(self) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"status": JobStatus.PENDING, "result": None, "error": None}
        return job_id

    def set_running(self, job_id: str):
        if job_id in self._jobs:
            self._jobs[job_id]["status"] = JobStatus.RUNNING

    def set_completed(self, job_id: str, result: Any):
        if job_id in self._jobs:
            self._jobs[job_id]["status"] = JobStatus.COMPLETED
            self._jobs[job_id]["result"] = result

    def set_failed(self, job_id: str, error: str):
        if job_id in self._jobs:
            self._jobs[job_id]["status"] = JobStatus.FAILED
            self._jobs[job_id]["error"] = error

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._jobs.get(job_id)


# Singleton
job_store = JobStore()
