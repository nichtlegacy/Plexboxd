from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from plexboxd.domain.enums import RatingJobStatus


@dataclass(slots=True)
class RatingJobWorker:
    watch_event_repository: object
    rating_job_service: object
    rating_execution_service: object
    success_callback: Callable[[object, object], None] | None = None
    failure_callback: Callable[[object, Exception], None] | None = None

    def run_once(self, worker_id: str) -> bool:
        job = self.rating_job_service.claim_next(worker_id=worker_id)
        if job is None:
            return False

        job = self.rating_job_service.mark_status(job, RatingJobStatus.RUNNING)
        event = self.watch_event_repository.get_by_id(job.watch_event_id)
        if event is None:
            error = RuntimeError(f"watch_event '{job.watch_event_id}' not found")
            self.rating_job_service.mark_status(job, RatingJobStatus.FAILED)
            if self.failure_callback is not None:
                self.failure_callback(job, error)
            raise error

        try:
            result = self.rating_execution_service.execute(job, event)
            updated_job = self.rating_job_service.mark_status(job, RatingJobStatus.SUCCEEDED)
            if self.success_callback is not None:
                self.success_callback(updated_job, result)
            return True
        except Exception as exc:
            failed_job = self.rating_job_service.mark_status(job, RatingJobStatus.FAILED)
            if self.failure_callback is not None:
                self.failure_callback(failed_job, exc)
            return True
