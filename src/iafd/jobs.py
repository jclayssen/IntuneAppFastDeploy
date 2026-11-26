"""Long tasks (download, upload to Intune) run in a thread and report progress."""

from __future__ import annotations

import secrets
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .appmodel import FormError
from .auth import AuthError
from .download import DownloadError
from .graph import GraphError
from .msi import MsiError
from .store import StoreError

JOB_TTL = 6 * 3600


@dataclass
class Job:
    id: str
    owner: str
    work_dir: Path
    state: str = "running"  # running | ready | publishing | done | error
    step: str = "step.download"
    steps: list[str] = field(default_factory=list)
    done_bytes: int = 0
    total_bytes: int | None = None
    error: dict | None = None
    result: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)  # server-side only, never sent to the browser
    created: float = field(default_factory=time.time)

    def set_step(self, step: str) -> None:
        self.step = step
        self.steps.append(step)
        self.done_bytes, self.total_bytes = 0, None

    def set_progress(self, done: int, total: int | None) -> None:
        self.done_bytes, self.total_bytes = done, total

    def public(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "step": self.step,
            "steps": self.steps,
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
            "error": self.error,
            "result": self.result,
        }


def describe_error(exc: Exception) -> dict:
    """Map an exception to a translation key plus technical detail."""
    if isinstance(exc, DownloadError | AuthError):
        return {"code": exc.code, "detail": exc.detail}
    if isinstance(exc, FormError | StoreError):
        return {"code": str(exc), "detail": ""}
    if isinstance(exc, MsiError):
        return {"code": "error.bad_msi", "detail": str(exc)}
    if isinstance(exc, GraphError):
        code = "error.graph_forbidden" if exc.status in (401, 403) else "error.graph"
        return {"code": code, "detail": f"{exc.code}: {exc.message}"}
    return {"code": "error.unexpected", "detail": f"{type(exc).__name__}: {exc}"}


class JobManager:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)

    def create(self, owner: str) -> Job:
        self.purge()
        job_id = secrets.token_urlsafe(12)
        job = Job(id=job_id, owner=owner, work_dir=self.work_dir / job_id)
        job.work_dir.mkdir(parents=True)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str, owner: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return job if job and job.owner == owner else None

    def remove(self, job: Job) -> None:
        with self._lock:
            self._jobs.pop(job.id, None)
        shutil.rmtree(job.work_dir, ignore_errors=True)

    def purge(self) -> None:
        limit = time.time() - JOB_TTL
        with self._lock:
            old = [job for job in self._jobs.values() if job.created < limit]
        for job in old:
            if job.state not in ("running", "publishing"):
                self.remove(job)

    def run(self, job: Job, work: Callable[[Job], None], final_state: str, busy_state: str) -> None:
        """Run ``work`` in a thread; any exception becomes a readable job error."""
        job.state = busy_state
        job.error = None

        def target() -> None:
            try:
                work(job)
                job.state = final_state
            except Exception as exc:  # noqa: BLE001 - reported to the user
                job.error = describe_error(exc)
                if job.error["code"] == "error.unexpected":
                    traceback.print_exc()
                # A failed publish can be retried from the review screen.
                job.state = "ready" if busy_state == "publishing" else "error"
            finally:
                if job.state in ("done", "error"):
                    shutil.rmtree(job.work_dir, ignore_errors=True)

        threading.Thread(target=target, daemon=True, name=f"job-{job.id}").start()
