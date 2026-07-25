from __future__ import annotations

from dataclasses import dataclass

from plexboxd.domain.enums import MatchStrategy
from plexboxd.domain.models import FilmMatch, MatchCandidate, WatchEvent


@dataclass(slots=True)
class MatchResolutionService:
    cache_repository: object
    provider: object

    def resolve(self, event: WatchEvent) -> FilmMatch | None:
        cached_match = self._resolve_from_cache(event)
        if cached_match is not None:
            return cached_match

        tmdb_match = self.provider.match_by_tmdb(event)
        if tmdb_match is not None:
            return tmdb_match

        candidates = tuple(self.provider.search_candidates(event))
        if not candidates:
            return None
        best_candidate = max(candidates, key=lambda candidate: candidate.score)
        return FilmMatch(
            letterboxd_film_id=best_candidate.letterboxd_film_id,
            letterboxd_slug=best_candidate.letterboxd_slug,
            strategy=MatchStrategy.SEARCH,
            confidence=best_candidate.score,
            letterboxd_lid=best_candidate.letterboxd_lid,
            candidates=candidates,
        )

    def _resolve_from_cache(self, event: WatchEvent) -> FilmMatch | None:
        """Return a cached match, but only if it carries the LID the write API needs.

        Rows cached before the API migration have no LID; those are backfilled from the
        slug when possible, and otherwise skipped so the caller re-resolves rather than
        writing with a null productionId.
        """
        if not event.tmdb_id:
            return None
        cached = self.cache_repository.get_by_tmdb_id(event.tmdb_id)
        if cached is None:
            return None

        slug = cached["letterboxd_slug"]
        lid = cached.get("letterboxd_lid")
        if not lid:
            backfill = getattr(self.provider, "resolve_lid_for_slug", None)
            lid = backfill(slug) if backfill is not None else None
            if not lid:
                return None
            self._persist_lid(cached, lid)

        return FilmMatch(
            letterboxd_film_id=cached["letterboxd_film_id"],
            letterboxd_slug=slug,
            strategy=MatchStrategy.CACHE,
            confidence=float(cached.get("confidence", 1.0)),
            letterboxd_lid=lid,
            candidates=(),
        )

    def _persist_lid(self, cached: dict, lid: str) -> None:
        updater = getattr(self.cache_repository, "set_lid", None)
        if updater is None:
            return
        updater(cached["id"], lid)


def build_candidate(
    *,
    film_id: str,
    slug: str,
    title: str,
    year: int | None,
    score: float,
    reason: str,
    lid: str | None = None,
) -> MatchCandidate:
    return MatchCandidate(
        letterboxd_film_id=film_id,
        letterboxd_slug=slug,
        candidate_title=title,
        candidate_year=year,
        score=score,
        decision_reason=reason,
        letterboxd_lid=lid,
    )
