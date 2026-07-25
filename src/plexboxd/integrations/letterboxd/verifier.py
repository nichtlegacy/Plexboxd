from __future__ import annotations


class VerificationError(RuntimeError):
    """Letterboxd stored something other than what was requested."""


class BasicLetterboxdVerifier:
    """Checks that Letterboxd stored what we asked it to store.

    The log-entry API echoes back the persisted rating, like flag and diary details, so
    a mismatch here means the write landed differently than requested and the job should
    fail rather than be recorded as a success.

    Note the requested date is deliberately not compared against ``event.watched_at``:
    the writer intentionally shifts it by a day when the watch happened before
    DATE_THRESHOLD_HOUR, so the authoritative expectation is the date the writer sent.
    """

    def verify(self, *, write_result, event, job) -> None:
        if not write_result:
            raise VerificationError("Letterboxd write returned no result payload")

        if not write_result.get("letterboxd_entry_id"):
            raise VerificationError("Letterboxd write returned no entry id")

        requested_date = write_result.get("watched_on")
        stored_date = write_result.get("diary_date")
        if stored_date is not None and requested_date is not None and stored_date != requested_date:
            raise VerificationError(
                f"Letterboxd diary date mismatch: requested {requested_date}, stored {stored_date}"
            )

        stored_rating = write_result.get("rating")
        if stored_rating is not None and not _ratings_match(stored_rating, job.requested_rating):
            raise VerificationError(
                f"Letterboxd rating mismatch: requested {job.requested_rating}, stored {stored_rating}"
            )

        stored_like = write_result.get("liked")
        if stored_like is not None and bool(stored_like) != bool(job.requested_liked):
            raise VerificationError(
                f"Letterboxd like mismatch: requested {job.requested_liked}, stored {stored_like}"
            )

        stored_rewatch = write_result.get("rewatch")
        if stored_rewatch is not None and bool(stored_rewatch) != bool(job.requested_rewatch):
            raise VerificationError(
                f"Letterboxd rewatch mismatch: requested {job.requested_rewatch}, stored {stored_rewatch}"
            )


def _ratings_match(stored, requested) -> bool:
    try:
        return abs(float(stored) - float(requested)) < 0.01
    except (TypeError, ValueError):
        return False
