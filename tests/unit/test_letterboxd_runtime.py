from datetime import date, datetime, timedelta, timezone

from plexboxd.domain.models import WatchEvent
from plexboxd.integrations.letterboxd.matcher import (
    _extract_candidates,
    _extract_film_id,
    _slug_from_url,
)
from plexboxd.integrations.letterboxd.session import LetterboxdSessionProvider
from plexboxd.integrations.letterboxd.writer import _build_log_entry_payload


def _response(*, text: str = "", status_code: int = 200, headers: dict | None = None):
    return type(
        "Response",
        (),
        {"text": text, "status_code": status_code, "headers": headers or {}},
    )()


def test_cloudflare_detection() -> None:
    provider = LetterboxdSessionProvider()
    challenge = _response(text="<title>Just a moment...</title> Cloudflare", status_code=403)
    rate_limited = _response(
        text="<title>Access denied | letterboxd.com used Cloudflare to restrict access</title><h1>Error1015</h1>",
    )

    assert provider._is_cloudflare_response(challenge) is True
    assert provider._is_cloudflare_response(rate_limited) is True


def test_cloudflare_detection_uses_cf_mitigated_header() -> None:
    """A challenge can arrive with an opaque body, so the header is authoritative."""
    provider = LetterboxdSessionProvider()
    challenge = _response(text="", status_code=403, headers={"cf-mitigated": "challenge"})

    assert provider._is_cloudflare_response(challenge) is True


def test_csrf_rejection_is_not_treated_as_cloudflare() -> None:
    """A JSON 403 from Letterboxd itself must not trigger profile rotation."""
    provider = LetterboxdSessionProvider()
    rejected = _response(text="Invalid CSRF token", status_code=403)

    assert provider._is_cloudflare_response(rejected) is False


def test_default_impersonation_profiles_exclude_challenged_ones() -> None:
    """chrome120 fails on POST and the bare "chrome" alias fails outright."""
    provider = LetterboxdSessionProvider()

    assert "chrome120" not in provider.impersonation_profiles
    assert "chrome" not in provider.impersonation_profiles
    assert provider.impersonation_profiles[0] == "chrome136"


def test_impersonation_profiles_are_configurable(monkeypatch) -> None:
    monkeypatch.setenv("LETTERBOXD_IMPERSONATE", "safari184, firefox135")

    assert LetterboxdSessionProvider().impersonation_profiles == ("safari184", "firefox135")


def test_logged_out_detection_ignores_static_analytics_stub() -> None:
    """letterboxd.com ships `loggedIn: false` to signed-in members too."""
    authenticated = '<body class="update js-update-screen">loggedIn: false role: "guest"</body>'

    assert LetterboxdSessionProvider._looks_logged_out(authenticated) is False


def test_logged_out_detection_uses_sign_in_flow_class() -> None:
    """Guests get a 200 with the sign-in flow body class, not a redirect."""
    guest = '<body class="screen-standalone-flow screen-standalone-flow-sign-in">'

    assert LetterboxdSessionProvider._looks_logged_out(guest) is True


def test_expired_cookies_are_not_replayed(monkeypatch, tmp_path) -> None:
    """Seeding an expired cookie would clobber a still-valid one and log us out."""
    import json as json_module

    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(
        json_module.dumps(
            {
                "cookies": [
                    {"name": "fresh", "value": "keep", "domain": "letterboxd.com", "expires": 4102444800},
                    {"name": "letterboxd.signed.in.as", "value": "stale", "domain": "letterboxd.com", "expires": 1},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LETTERBOXD_SESSION_FILE", str(cookie_file))

    session = LetterboxdSessionProvider()._load_persisted_session()

    assert session is not None
    names = {cookie.name for cookie in session.cookies.jar}
    assert "fresh" in names
    assert "letterboxd.signed.in.as" not in names


def test_cookie_file_is_resolved_after_dotenv_load(monkeypatch, tmp_path) -> None:
    """The path must not be captured at import time.

    load_dotenv() runs during container bootstrap, so a module-level constant would
    freeze the pre-.env value while the browser writer resolved the post-.env one,
    leaving the two halves reading different files.
    """
    from plexboxd.integrations.letterboxd import session as session_module

    monkeypatch.setenv("LETTERBOXD_SESSION_FILE", str(tmp_path / "late.json"))

    assert session_module.cookie_file() == tmp_path / "late.json"


def test_browser_and_http_agree_on_cookie_path(monkeypatch, tmp_path) -> None:
    """Both halves of the hybrid flow must read and write the same cookie bundle."""
    from plexboxd.integrations.letterboxd import browser_fallback
    from plexboxd.integrations.letterboxd import session as session_module

    monkeypatch.setenv("LETTERBOXD_SESSION_FILE", str(tmp_path / "shared.json"))
    monkeypatch.setattr(
        browser_fallback,
        "_resolve_browser_node_module",
        lambda preferred_package: (tmp_path, preferred_package),
    )
    monkeypatch.setattr(browser_fallback, "_resolve_browser_executable", lambda: None)

    assert browser_fallback.BrowserLetterboxdClient().cookie_file == session_module.cookie_file()


def test_extract_film_id_and_slug_from_tmdb_page() -> None:
    html = '<html><body><div data-film-id="27470"></div></body></html>'

    assert _extract_film_id(html) == "27470"
    assert _slug_from_url("https://letterboxd.com/film/human-traffic/") == "human-traffic"


def test_extract_film_id_from_production_uid() -> None:
    """Current film pages expose the numeric id only via data-production-uid."""
    html = '<html><body><div data-production-uid="film:386613"></div></body></html>'

    assert _extract_film_id(html) == "386613"


def test_extract_candidates_scores_exact_match_first(fixed_now) -> None:
    html = """
    <ul>
      <li data-film-id="27470" data-target-link="/film/human-traffic/" data-film-name="Human Traffic" data-film-release-year="1999"></li>
      <li data-film-id="99999" data-target-link="/film/traffic/" data-film-name="Traffic" data-film-release-year="2000"></li>
    </ul>
    """
    event = WatchEvent(
        id="watch-1",
        plex_rating_key="rk-1",
        title="Human Traffic",
        original_title="Human Traffic",
        tmdb_id="11129",
        year=1999,
        watched_at=fixed_now,
        detected_at=fixed_now,
    )

    candidates = _extract_candidates(html, event)

    assert [candidate.letterboxd_film_id for candidate in candidates] == ["27470", "99999"]
    assert candidates[0].score > candidates[1].score
    assert "title_exact" in candidates[0].decision_reason


def test_build_log_entry_payload_uses_lid_and_float_rating() -> None:
    payload = _build_log_entry_payload(
        production_id="gdKW",
        rating=4.5,
        liked=True,
        rewatch=False,
        watched_on=date(2026, 5, 7),
    )

    assert payload == {
        "productionId": "gdKW",
        "diaryDetails": {"diaryDate": "2026-05-07", "rewatch": False},
        "tags": [],
        "like": True,
        "rating": 4.5,
    }


def test_build_log_entry_payload_omits_rating_when_unset() -> None:
    """An unrated diary entry must not send rating: 0."""
    payload = _build_log_entry_payload(
        production_id="gdKW",
        rating=0,
        liked=False,
        rewatch=False,
        watched_on=date(2026, 5, 7),
    )

    assert "rating" not in payload


def test_build_log_entry_payload_includes_tags_and_review() -> None:
    payload = _build_log_entry_payload(
        production_id="gdKW",
        rating=3.0,
        liked=False,
        rewatch=True,
        watched_on=date(2026, 5, 7),
        tags=("plex", " imported ", ""),
        review="  Great film.  ",
    )

    assert payload["tags"] == ["plex", "imported"]
    assert payload["review"] == {"text": "Great film.", "containsSpoilers": False}
    assert payload["diaryDetails"]["rewatch"] is True


def test_build_log_entry_payload_omits_empty_review() -> None:
    payload = _build_log_entry_payload(
        production_id="gdKW",
        rating=3.0,
        liked=False,
        rewatch=False,
        watched_on=date(2026, 5, 7),
        review="   ",
    )

    assert "review" not in payload


def test_late_night_viewing_counts_as_the_previous_day() -> None:
    """A film finished at 02:00 belongs to the evening before in a diary."""
    from plexboxd.integrations.letterboxd.writer import _normalize_watched_on

    assert _normalize_watched_on(datetime(2026, 7, 26, 2, 0), 7) == date(2026, 7, 25)
    assert _normalize_watched_on(datetime(2026, 7, 26, 6, 59), 7) == date(2026, 7, 25)


def test_viewing_after_the_threshold_keeps_its_own_day() -> None:
    from plexboxd.integrations.letterboxd.writer import _normalize_watched_on

    assert _normalize_watched_on(datetime(2026, 7, 26, 7, 0), 7) == date(2026, 7, 26)
    assert _normalize_watched_on(datetime(2026, 7, 26, 23, 30), 7) == date(2026, 7, 26)


def test_diary_date_does_not_depend_on_when_you_rate() -> None:
    """The decision used to read datetime.now(), so the same viewing drifted.

    Rated at 01:10 it shifted back a day; rated the same afternoon it did not.
    """
    from plexboxd.integrations.letterboxd.writer import _normalize_watched_on

    viewing = datetime(2026, 7, 26, 1, 0)
    # Same input, no reference to the current time anywhere in the call.
    assert _normalize_watched_on(viewing, 7) == date(2026, 7, 25)
    assert _normalize_watched_on(viewing, 7) == _normalize_watched_on(viewing, 7)


def test_threshold_of_zero_disables_the_shift() -> None:
    from plexboxd.integrations.letterboxd.writer import _normalize_watched_on

    assert _normalize_watched_on(datetime(2026, 7, 26, 0, 30), 0) == date(2026, 7, 26)


def test_aware_datetimes_are_read_on_the_local_clock() -> None:
    """"Before 07:00" is only meaningful in local time."""
    from plexboxd.integrations.letterboxd.writer import _normalize_watched_on

    # 23:00 UTC is 01:00 the next day in UTC+2 — a late-night viewing there.
    aware = datetime(2026, 7, 25, 23, 0, tzinfo=timezone(timedelta(hours=2))) 
    assert _normalize_watched_on(aware, 7) == date(2026, 7, 25)


def test_a_plain_date_is_taken_as_given() -> None:
    """Without an hour there is nothing to decide, so it must not shift blindly."""
    from plexboxd.integrations.letterboxd.writer import _normalize_watched_on

    assert _normalize_watched_on(date(2026, 7, 26), 7) == date(2026, 7, 26)
