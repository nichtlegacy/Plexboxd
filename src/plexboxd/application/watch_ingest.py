from __future__ import annotations

from dataclasses import dataclass

from plexboxd.domain.models import WatchEvent


class WatchEventConflictError(Exception):
    """Raised when the same watch event is ingested twice."""


@dataclass(slots=True)
class WatchIngestService:
    """Creates watch events while delegating idempotency to a repository."""

    repository: object

    def ingest(self, event: WatchEvent) -> WatchEvent:
        existing = self.repository.get_by_event_identity(
            plex_rating_key=event.plex_rating_key,
            watched_at=event.watched_at,
        )
        if existing is not None:
            return existing
        return self.repository.add(event)
