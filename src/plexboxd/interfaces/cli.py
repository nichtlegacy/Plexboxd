from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Sequence

from plexboxd.domain.enums import RatingJobStatus
from plexboxd.domain.models import RatingRequest
from plexboxd.infrastructure.bootstrap import build_application_container
from plexboxd.infrastructure.queue.worker import RatingJobWorker
from plexboxd.integrations.letterboxd.session import LetterboxdSessionProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plexboxd-cli")
    parser.add_argument("--db-path", default="data/plexboxd.db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect-job")
    inspect_parser.add_argument("job_id")

    subparsers.add_parser("list-failed-jobs")

    retry_parser = subparsers.add_parser("retry-job")
    retry_parser.add_argument("job_id")

    subparsers.add_parser("verify-session")
    subparsers.add_parser("bootstrap-session")

    smoke_parser = subparsers.add_parser("smoke-write")
    smoke_parser.add_argument("--watch-event-id", required=True)
    smoke_parser.add_argument("--rating", required=True, type=float)
    smoke_parser.add_argument("--liked", action="store_true")
    smoke_parser.add_argument("--rewatch", action="store_true")
    smoke_parser.add_argument("--tags", default="", help="Comma- or space-separated tags")
    smoke_parser.add_argument("--review", default="")
    smoke_parser.add_argument("--dry-run", action="store_true")

    return parser


def _split_tags(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part for part in (piece.strip() for piece in raw.replace(",", " ").split()) if part)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    container = build_application_container(args.db_path)

    if args.command == "inspect-job":
        job = container.rating_jobs.get_by_id(args.job_id)
        if job is None:
            print(json.dumps({"error": "job_not_found", "job_id": args.job_id}))
            return 1
        print(json.dumps(asdict(job), default=str, indent=2))
        return 0

    if args.command == "list-failed-jobs":
        jobs = container.rating_jobs.list_by_status(RatingJobStatus.FAILED)
        print(json.dumps([asdict(job) for job in jobs], default=str, indent=2))
        return 0

    if args.command == "retry-job":
        job = container.rating_jobs.requeue(args.job_id, container.clock.now())
        print(json.dumps({"job_id": job.id, "status": job.status.value}, indent=2))
        return 0

    if args.command in {"verify-session", "bootstrap-session"}:
        provider = LetterboxdSessionProvider()
        if args.command == "bootstrap-session":
            provider.bootstrap()
        else:
            provider.verify()
        print(json.dumps({"status": "ok", "command": args.command}, indent=2))
        return 0

    if args.command == "smoke-write":
        event = container.watch_events.get_by_id(args.watch_event_id)
        if event is None:
            print(json.dumps({"error": "watch_event_not_found", "watch_event_id": args.watch_event_id}))
            return 1

        if args.dry_run:
            match = container.matching_service.resolve(event)
            if match is None:
                print(json.dumps({"status": "no_match", "watch_event_id": event.id}, indent=2))
                return 2
            print(
                json.dumps(
                    {
                        "status": "match_resolved",
                        "watch_event_id": event.id,
                        "letterboxd_film_id": match.letterboxd_film_id,
                        "letterboxd_lid": match.letterboxd_lid,
                        "letterboxd_slug": match.letterboxd_slug,
                        "strategy": match.strategy.value,
                        "confidence": match.confidence,
                    },
                    indent=2,
                )
            )
            return 0

        request = RatingRequest(
            rating=args.rating,
            liked=args.liked,
            rewatch=args.rewatch,
            requested_by_discord_user_id="cli-smoke",
            tags=_split_tags(args.tags),
            review=args.review,
        )
        job = container.rating_job_service.enqueue(event.id, None, request)
        failures: list[str] = []
        worker = RatingJobWorker(
            watch_event_repository=container.watch_events,
            rating_job_service=container.rating_job_service,
            rating_execution_service=container.rating_execution_service,
            failure_callback=lambda _job, exc: failures.append(f"{type(exc).__name__}: {exc}"),
        )
        worker.run_once(worker_id="cli-smoke")
        updated = container.rating_jobs.get_by_id(job.id)
        status = updated.status.value if updated else "unknown"
        report: dict = {"job_id": job.id, "status": status}
        if failures:
            report["errors"] = failures
        result = container.rating_results.get_by_watch_event(event.id)
        if result is not None:
            report["letterboxd_entry_id"] = result.letterboxd_entry_id
            report["rating_value"] = result.rating_value
            report["watched_on"] = result.watched_on.isoformat()
        print(json.dumps(report, indent=2))
        return 0 if status == "succeeded" else 1

    parser.error(f"Unhandled command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
