from __future__ import annotations

import json
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from plexboxd.application.matching import build_candidate
from plexboxd.domain.enums import MatchStrategy
from plexboxd.domain.models import FilmMatch, MatchCandidate, WatchEvent

from .session import BASE_URL, LetterboxdSessionProvider


class LetterboxdMatcher:
    def __init__(self, session_provider: LetterboxdSessionProvider | None = None) -> None:
        self.session_provider = session_provider or LetterboxdSessionProvider()

    def match_by_tmdb(self, event: WatchEvent) -> FilmMatch | None:
        if not event.tmdb_id:
            return None

        with self.session_provider.open_public() as session:
            response = self.session_provider.get(
                f"{BASE_URL}/tmdb/{event.tmdb_id}",
                session=session,
                allow_redirects=True,
            )
            slug = _slug_from_url(str(response.url))
            film_id, lid = self._resolve_identifiers(slug, response.text, session)
        if not slug or (film_id is None and lid is None):
            return None

        return FilmMatch(
            letterboxd_film_id=film_id or lid,
            letterboxd_slug=slug,
            strategy=MatchStrategy.TMDB,
            confidence=1.0,
            letterboxd_lid=lid,
            candidates=(),
        )

    def search_candidates(self, event: WatchEvent) -> list[MatchCandidate]:
        query = quote_plus(f"{event.original_title or event.title} {event.year or ''}".strip())
        with self.session_provider.open_public() as session:
            response = self.session_provider.get(
                f"{BASE_URL}/search/films/{query}/",
                session=session,
                allow_redirects=True,
            )
            if "/film/" in str(response.url):
                slug = _slug_from_url(str(response.url))
                film_id, lid = self._resolve_identifiers(slug, response.text, session)
                if slug and (film_id or lid):
                    return [
                        build_candidate(
                            film_id=film_id or lid,
                            slug=slug,
                            title=event.original_title or event.title,
                            year=event.year,
                            score=0.9,
                            reason="direct_search_redirect",
                            lid=lid,
                        )
                    ]

            return _extract_candidates(
                response.text,
                event,
                identifier_resolver=lambda slug: self._resolve_identifiers(slug, "", session),
            )

    def _resolve_identifiers(self, slug: str, html: str, session) -> tuple[str | None, str | None]:
        """Return ``(numeric_film_id, lid)`` for a film slug.

        The numeric id can often be scraped from the page, but the base-62 ``lid`` is
        only exposed by ``/film/<slug>/json/`` and is what the write API requires, so
        that endpoint is the source of truth for both.
        """
        scraped_film_id = _extract_film_id(html) if html else None
        if not slug:
            return scraped_film_id, None

        payload = self._fetch_film_json(slug, session)
        if payload is None:
            return scraped_film_id, None

        film_id = payload.get("id")
        lid = payload.get("lid")
        return (str(film_id) if film_id else scraped_film_id), (str(lid) if lid else None)

    def _fetch_film_json(self, slug: str, session) -> dict | None:
        response = self.session_provider.get(
            f"{BASE_URL}/film/{slug}/json/",
            session=session,
            allow_redirects=True,
            headers={"Accept": "application/json, text/javascript, */*; q=0.01"},
        )
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def resolve_lid_for_slug(self, slug: str) -> str | None:
        """Public lookup used to backfill a LID for a cached match."""
        with self.session_provider.open_public() as session:
            _, lid = self._resolve_identifiers(slug, "", session)
        return lid


def _extract_film_id(html: str) -> str | None:
    stripped = html.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = {}
        film_id = payload.get("id")
        if film_id:
            return str(film_id)

    soup = BeautifulSoup(html, "html.parser")
    element = soup.find(attrs={"data-film-id": True})
    if element and element.get("data-film-id"):
        return str(element["data-film-id"])

    production = soup.find(attrs={"data-production-uid": True})
    if production and production.get("data-production-uid", "").startswith("film:"):
        return str(production["data-production-uid"]).split("film:", 1)[1]
    return None


def _extract_candidates(html: str, event: WatchEvent, identifier_resolver=None) -> list[MatchCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[MatchCandidate] = []
    seen_slugs: set[str] = set()

    for element in soup.select("[data-film-id], [data-production-uid], [data-item-slug]"):
        slug = _extract_slug(element)
        if not slug or slug in seen_slugs:
            continue

        film_id = str(element.get("data-film-id") or "").strip()
        if not film_id:
            film_id = _film_id_from_production_uid(element) or ""
        lid = None
        if identifier_resolver is not None:
            resolved_id, lid = identifier_resolver(slug)
            film_id = film_id or (resolved_id or "")
        if not film_id and not lid:
            continue

        title = _extract_title(element)
        year = _extract_year(element)
        score, reasons = _score_candidate(event, title, year, slug)
        seen_slugs.add(slug)
        candidates.append(
            build_candidate(
                film_id=film_id or lid,
                slug=slug,
                title=title,
                year=year,
                score=score,
                reason=",".join(reasons),
                lid=lid,
            )
        )

    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates


def _film_id_from_production_uid(element) -> str | None:
    uid = str(element.get("data-production-uid") or "")
    if uid.startswith("film:"):
        return uid.split("film:", 1)[1]
    return None


def _extract_slug(element) -> str:
    for value in (
        element.get("data-target-link"),
        element.get("data-item-link"),
        element.get("data-film-slug"),
        f"/film/{element.get('data-item-slug')}/" if element.get("data-item-slug") else None,
    ):
        if value:
            return _slug_from_url(str(value))

    anchor = element.find("a", href=True)
    if anchor and anchor.get("href"):
        return _slug_from_url(anchor["href"])

    parent_anchor = element.find_parent("a", href=True)
    if parent_anchor and parent_anchor.get("href"):
        return _slug_from_url(parent_anchor["href"])
    return ""


def _extract_title(element) -> str:
    for value in (
        element.get("data-item-name"),
        element.get("data-film-name"),
        element.get("data-original-title"),
    ):
        if value:
            return str(value).strip()

    image = element.find("img", alt=True)
    if image and image.get("alt"):
        return str(image["alt"]).strip()

    title_link = element.find("a")
    if title_link and title_link.get_text(strip=True):
        return title_link.get_text(strip=True)
    return ""


def _extract_year(element) -> int | None:
    for value in (
        element.get("data-film-release-year"),
        element.get("data-release-year"),
    ):
        parsed = _parse_year(value)
        if parsed is not None:
            return parsed

    year_tag = element.find(attrs={"class": lambda classes: classes and "year" in str(classes)})
    if year_tag:
        parsed = _parse_year(year_tag.get_text(strip=True))
        if parsed is not None:
            return parsed
    return None


def _parse_year(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 4 and text.isdigit():
        return int(text)
    return None


def _score_candidate(event: WatchEvent, title: str, year: int | None, slug: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    normalized_title = _normalize_text(title)
    target_titles = {value for value in (_normalize_text(event.title), _normalize_text(event.original_title or "")) if value}

    if normalized_title and normalized_title in target_titles:
        score += 0.7
        reasons.append("title_exact")
    elif normalized_title and any(normalized_title in target or target in normalized_title for target in target_titles):
        score += 0.45
        reasons.append("title_partial")

    if year is not None and event.year is not None:
        if year == event.year:
            score += 0.25
            reasons.append("year_exact")
        elif abs(year - event.year) == 1:
            score += 0.05
            reasons.append("year_near")

    if slug and any(token in slug for token in _slug_tokens(event)):
        score += 0.05
        reasons.append("slug_match")

    if not reasons:
        reasons.append("fallback")
    return score, reasons


def _slug_tokens(event: WatchEvent) -> set[str]:
    titles = [event.title, event.original_title or ""]
    tokens: set[str] = set()
    for title in titles:
        normalized = _normalize_text(title)
        if normalized:
            tokens.add(normalized.replace(" ", "-"))
    return tokens


def _normalize_text(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "film":
        return parts[1]
    return ""
