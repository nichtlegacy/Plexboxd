from __future__ import annotations

import argparse

from plexboxd.infrastructure.bootstrap import build_application_container
from plexboxd.infrastructure.queue.worker import RatingJobWorker


def main() -> int:
    parser = argparse.ArgumentParser(prog="plexboxd-worker")
    parser.add_argument("--db-path", default="data/plexboxd.db")
    parser.add_argument("--worker-id", default="cli-worker")
    args = parser.parse_args()

    container = build_application_container(args.db_path)
    worker = RatingJobWorker(
        watch_event_repository=container.watch_events,
        rating_job_service=container.rating_job_service,
        rating_execution_service=container.rating_execution_service,
    )
    processed = worker.run_once(worker_id=args.worker_id)
    return 0 if processed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
