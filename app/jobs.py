"""Tiny in-memory background job registry (threads + polling)."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Callable


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str, project_id: str | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.project_id = project_id
        self.status = "queued"  # queued | running | done | error | cancelled
        self.progress = 0.0  # 0..1, or -1 for indeterminate
        self.message = ""
        self.result = None
        self.error: str | None = None
        self.created = time.time()
        self._cancel = threading.Event()
        self.on_cancel: Callable[[], None] | None = None
        # Files/folders to delete if the app exits while this job is unfinished.
        self.cleanup_paths: list = []

    def update(self, progress: float | None = None, message: str | None = None):
        if progress is not None:
            self.progress = progress
        if message is not None:
            self.message = message
        self.check()

    def check(self):
        if self._cancel.is_set():
            raise Cancelled()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self):
        self._cancel.set()
        if self.on_cancel:
            try:
                self.on_cancel()
            except Exception:
                pass

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "projectId": self.project_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, fn: Callable[[Job], object], project_id: str | None = None) -> Job:
        job = Job(kind, project_id)
        with self._lock:
            self._jobs[job.id] = job

        def run():
            job.status = "running"
            try:
                job.result = fn(job)
                job.status = "done"
                job.progress = 1.0
            except Cancelled:
                job.status = "cancelled"
                job.message = "Cancelled"
            except Exception as e:  # surfaced to the UI
                traceback.print_exc()
                job.status = "error"
                job.error = str(e) or e.__class__.__name__

        threading.Thread(target=run, daemon=True, name=f"job-{kind}-{job.id}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.status in ("queued", "running")]
