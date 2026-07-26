from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta

from .browser_fallback import BrowserLetterboxdClient
from .session import (
    BASE_URL,
    AuthenticationError,
    CloudflareChallengeError,
    CsrfTokenError,
    LetterboxdSessionError,
    LetterboxdSessionProvider,
)


# Letterboxd retired the form endpoint POST /s/save-diary-entry (it now 404s) and
# replaced it with this JSON API.
LOG_ENTRY_PATH = "/api/v0/production-log-entries"

logger = logging.getLogger("LetterboxdIntegration")


class LetterboxdWriter:
    def __init__(self, session_provider: LetterboxdSessionProvider | None = None) -> None:
        self.session_provider = session_provider or LetterboxdSessionProvider()
        self.date_threshold_hour = int(os.getenv("DATE_THRESHOLD_HOUR", "7"))
        self.browser_enabled = os.getenv("LETTERBOXD_BROWSER_FALLBACK", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.browser_writer = BrowserLetterboxdClient() if self.browser_enabled else None

    def write(
        self,
        *,
        letterboxd_film_id: str,
        letterboxd_slug: str,
        rating: float,
        liked: bool,
        rewatch: bool,
        watched_on: date | datetime,
        letterboxd_lid: str | None = None,
        tags: tuple[str, ...] | list[str] = (),
        review: str = "",
    ) -> dict:
        normalized_watched_on = _normalize_watched_on(watched_on, self.date_threshold_hour)
        production_id = letterboxd_lid or letterboxd_film_id
        payload = _build_log_entry_payload(
            production_id=production_id,
            rating=rating,
            liked=liked,
            rewatch=rewatch,
            watched_on=normalized_watched_on,
            tags=tags,
            review=review,
        )
        referer = f"{BASE_URL}/film/{letterboxd_slug}/"

        try:
            return self._write_via_session(payload, referer=referer, watched_on=normalized_watched_on)
        except (CloudflareChallengeError, AuthenticationError, CsrfTokenError) as exc:
            if self.browser_writer is None:
                raise
            logger.warning("Session write blocked (%s); refreshing Letterboxd session via browser", exc)
            self.browser_writer.bootstrap()

            try:
                return self._write_via_session(payload, referer=referer, watched_on=normalized_watched_on)
            except LetterboxdSessionError as retry_exc:
                logger.warning("Session write still failing after refresh (%s); writing via browser", retry_exc)
                return self.browser_writer.write(
                    letterboxd_film_id=letterboxd_film_id,
                    letterboxd_slug=letterboxd_slug,
                    rating=rating,
                    liked=liked,
                    rewatch=rewatch,
                    watched_on=normalized_watched_on,
                    letterboxd_lid=letterboxd_lid,
                    tags=tags,
                    review=review,
                )

    def _write_via_session(self, payload: dict, *, referer: str, watched_on: date) -> dict:
        with self.session_provider.open() as authenticated:
            if not self.session_provider.has_clearance(authenticated.session):
                # /api/v0 writes are refused without a browser-minted cf_clearance cookie.
                raise CloudflareChallengeError("Letterboxd session has no Cloudflare clearance cookie")
            response = self.session_provider.api_post(
                LOG_ENTRY_PATH,
                session=authenticated.session,
                json_body=payload,
                referer=referer,
            )

        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LetterboxdSessionError(
                f"Letterboxd log entry write returned non-JSON response: status={response.status_code}"
            ) from exc

        log_entry = body.get("logEntry")
        if not isinstance(log_entry, dict):
            message = body.get("message") or body.get("messages") or f"HTTP {response.status_code}"
            raise LetterboxdSessionError(f"Letterboxd log entry write rejected: {message}")

        return _result_from_log_entry(log_entry, watched_on=watched_on, write_strategy="session")


def _build_log_entry_payload(
    *,
    production_id: str,
    rating: float,
    liked: bool,
    rewatch: bool,
    watched_on: date,
    tags: tuple[str, ...] | list[str] = (),
    review: str = "",
) -> dict:
    payload: dict = {
        "productionId": str(production_id),
        "diaryDetails": {
            "diaryDate": watched_on.isoformat(),
            "rewatch": bool(rewatch),
        },
        "tags": [tag.strip() for tag in tags if tag and tag.strip()],
        "like": bool(liked),
    }
    # The API takes stars directly as a float (2.5 == half a star), unlike the old
    # form endpoint which expected int(rating * 2).
    if rating:
        payload["rating"] = float(rating)
    if review and review.strip():
        payload["review"] = {"text": review.strip(), "containsSpoilers": False}
    return payload


def _result_from_log_entry(log_entry: dict, *, watched_on: date, write_strategy: str) -> dict:
    return {
        "write_strategy": write_strategy,
        "letterboxd_entry_id": log_entry.get("id"),
        "watched_on": watched_on.isoformat(),
        "rating": log_entry.get("rating"),
        "liked": log_entry.get("like"),
        "rewatch": (log_entry.get("diaryDetails") or {}).get("rewatch"),
        "diary_date": (log_entry.get("diaryDetails") or {}).get("diaryDate"),
        "entry_url": _entry_url(log_entry),
        "response": log_entry,
    }


def _entry_url(log_entry: dict) -> str | None:
    for link in log_entry.get("links") or []:
        if isinstance(link, dict) and link.get("url"):
            return str(link["url"])
    return None


def _normalize_watched_on(watched_on: date | datetime, threshold_hour: int) -> date:
    """Assign a late-night viewing to the day it started.

    A film finished at 02:00 belongs to the previous evening in a diary, so anything
    before DATE_THRESHOLD_HOUR shifts back a day.

    The decision is made from the *viewing* time. It used to compare
    ``datetime.now()`` — the moment the rating was submitted — against the watch date,
    which made the result depend on when you happened to click: the same 01:00 viewing
    landed on the previous day if rated immediately, but on the current day if rated that
    afternoon. Only a datetime carries the hour, so a plain date is taken as-is.
    """
    if isinstance(watched_on, datetime):
        # "Before 07:00" only means anything on a local clock, so convert an aware value
        # to local time first. Plex reports naive local times, which are used as they are.
        local = watched_on.astimezone() if watched_on.tzinfo is not None else watched_on
        if local.hour < threshold_hour:
            return (local - timedelta(days=1)).date()
        return local.date()
    return watched_on
