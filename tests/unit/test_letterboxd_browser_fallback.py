from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from plexboxd.integrations.letterboxd import browser_fallback
from plexboxd.integrations.letterboxd.session import CloudflareChallengeError
from plexboxd.integrations.letterboxd.writer import LetterboxdWriter


def test_browser_navigation_retries_err_aborted_once() -> None:
    script_path = Path(__file__).parents[2] / "src/plexboxd/integrations/letterboxd/scripts/letterboxd_browser.cjs"
    program = f"""
const {{ gotoWithRetry }} = require({json.dumps(str(script_path))});
let calls = 0;
const page = {{
  goto: async () => {{
    calls += 1;
    if (calls === 1) throw new Error('page.goto: net::ERR_ABORTED');
    return 'ok';
  }},
  waitForTimeout: async () => {{}},
}};
gotoWithRetry(page, 'https://letterboxd.com/activity/').then(() => {{
  if (calls !== 2) process.exit(1);
}}).catch(() => process.exit(2));
"""

    completed = subprocess.run(["node", "-e", program], check=False)

    assert completed.returncode == 0


def test_browser_client_bootstrap_uses_xvfb_and_persists_cookies(monkeypatch, tmp_path) -> None:
    cookie_file = tmp_path / "letterboxd_cookies.json"
    profile_dir = tmp_path / "profile"
    captured: dict[str, object] = {}

    monkeypatch.setenv("LETTERBOXD_USERNAME", "jan")
    monkeypatch.setenv("LETTERBOXD_PASSWORD", "secret")
    monkeypatch.setenv("LETTERBOXD_SESSION_FILE", str(cookie_file))
    monkeypatch.setenv("LETTERBOXD_BROWSER_PROFILE_DIR", str(profile_dir))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        browser_fallback,
        "_resolve_browser_node_module",
        lambda preferred_package: (Path("/tmp/node_modules"), preferred_package),
    )
    monkeypatch.setattr(browser_fallback, "_resolve_browser_executable", lambda: None)
    monkeypatch.setattr(browser_fallback, "which", lambda name: "/usr/bin/xvfb-run" if name == "xvfb-run" else None)

    def fake_run(command, capture_output, text, check, env):
        captured["command"] = command
        captured["env"] = env
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "command": "bootstrap",
                    "ok": True,
                    "signedInAs": "jan",
                    "cookies": [{"name": "letterboxd.signed.in.as", "value": "jan", "path": "/"}],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(browser_fallback.subprocess, "run", fake_run)

    client = browser_fallback.BrowserLetterboxdClient()
    payload = client.bootstrap()

    assert payload["signedInAs"] == "jan"
    assert captured["command"][:2] == ["xvfb-run", "-a"]
    assert "--headless" in captured["command"]
    assert "false" in captured["command"]
    assert captured["env"]["LETTERBOXD_BROWSER_PACKAGE"] == "patchright"
    assert cookie_file.exists()
    assert json.loads(cookie_file.read_text(encoding="utf-8"))["signed_in_as"] == "jan"


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _StubSessionProvider:
    """Minimal stand-in for LetterboxdSessionProvider around api_post."""

    def __init__(self, response=None, error: Exception | None = None, clearance: bool = True) -> None:
        self.response = response
        self.error = error
        self.clearance = clearance
        self.calls: list[dict] = []

    @contextmanager
    def open(self, **_kwargs):
        yield SimpleNamespace(session=object(), csrf_token="csrf-1", username="jan")

    def has_clearance(self, _session) -> bool:
        return self.clearance

    def api_post(self, path, *, session, json_body, referer):
        self.calls.append({"path": path, "json_body": json_body, "referer": referer})
        if self.error is not None:
            raise self.error
        return self.response


def _log_entry_response(entry_id: str = "fqHMA7", rating: float = 4.5) -> _FakeResponse:
    return _FakeResponse(
        {
            "logEntry": {
                "id": entry_id,
                "rating": rating,
                "like": True,
                "diaryDetails": {"diaryDate": "2026-05-07", "rewatch": False},
                "links": [{"type": "letterboxd", "url": f"https://letterboxd.com/jan/film/human-traffic/{entry_id}/"}],
            }
        }
    )


def test_writer_uses_session_first(monkeypatch) -> None:
    """The HTTP write is the primary path; the browser is only a recovery step."""

    class UnusedBrowserClient:
        def write(self, **_kwargs):  # pragma: no cover - must not be called
            raise AssertionError("browser write should not run when the session write succeeds")

        def bootstrap(self):  # pragma: no cover - must not be called
            raise AssertionError("browser bootstrap should not run when the session write succeeds")

    monkeypatch.setattr("plexboxd.integrations.letterboxd.writer.BrowserLetterboxdClient", UnusedBrowserClient)
    monkeypatch.setenv("LETTERBOXD_BROWSER_FALLBACK", "true")

    provider = _StubSessionProvider(response=_log_entry_response())
    writer = LetterboxdWriter(session_provider=provider)
    result = writer.write(
        letterboxd_film_id="27470",
        letterboxd_lid="gdKW",
        letterboxd_slug="human-traffic",
        rating=4.5,
        liked=True,
        rewatch=False,
        watched_on=date(2026, 5, 7),
    )

    assert result["write_strategy"] == "session"
    assert result["letterboxd_entry_id"] == "fqHMA7"
    assert provider.calls[0]["path"] == "/api/v0/production-log-entries"
    # productionId must be the base-62 LID, not the numeric film id.
    assert provider.calls[0]["json_body"]["productionId"] == "gdKW"
    assert provider.calls[0]["referer"] == "https://letterboxd.com/film/human-traffic/"


def test_writer_bootstraps_browser_then_retries_session(monkeypatch) -> None:
    """A blocked write should refresh cf_clearance and retry over HTTP, not fall straight to the browser."""
    events: list[str] = []

    class RecoveringProvider(_StubSessionProvider):
        def api_post(self, path, *, session, json_body, referer):
            events.append("api_post")
            if len(events) == 1:
                raise CloudflareChallengeError("challenged")
            return _log_entry_response()

    class RefreshingBrowserClient:
        def bootstrap(self):
            events.append("bootstrap")
            return {"ok": True}

        def write(self, **_kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("browser write should not run when the retry succeeds")

    monkeypatch.setattr("plexboxd.integrations.letterboxd.writer.BrowserLetterboxdClient", RefreshingBrowserClient)
    monkeypatch.setenv("LETTERBOXD_BROWSER_FALLBACK", "true")

    writer = LetterboxdWriter(session_provider=RecoveringProvider())
    result = writer.write(
        letterboxd_film_id="27470",
        letterboxd_lid="gdKW",
        letterboxd_slug="human-traffic",
        rating=4.5,
        liked=True,
        rewatch=False,
        watched_on=date(2026, 5, 7),
    )

    assert result["write_strategy"] == "session"
    assert events == ["api_post", "bootstrap", "api_post"]


def test_writer_falls_back_to_browser_when_session_stays_blocked(monkeypatch) -> None:
    class BrowserClient:
        def bootstrap(self):
            return {"ok": True}

        def write(self, **kwargs):
            return {
                "write_strategy": "browser",
                "letterboxd_entry_id": "entry-1",
                "watched_on": kwargs["watched_on"].isoformat(),
            }

    monkeypatch.setattr("plexboxd.integrations.letterboxd.writer.BrowserLetterboxdClient", BrowserClient)
    monkeypatch.setenv("LETTERBOXD_BROWSER_FALLBACK", "true")

    provider = _StubSessionProvider(error=CloudflareChallengeError("challenged"))
    writer = LetterboxdWriter(session_provider=provider)
    result = writer.write(
        letterboxd_film_id="27470",
        letterboxd_lid="gdKW",
        letterboxd_slug="human-traffic",
        rating=4.5,
        liked=True,
        rewatch=False,
        watched_on=date(2026, 5, 7),
    )

    assert result["write_strategy"] == "browser"


def test_writer_requires_clearance_before_writing(monkeypatch) -> None:
    """Without cf_clearance the API refuses the write, so refresh instead of trying."""

    class BrowserClient:
        def __init__(self) -> None:
            self.bootstrapped = False

        def bootstrap(self):
            self.bootstrapped = True

        def write(self, **kwargs):
            return {"write_strategy": "browser", "letterboxd_entry_id": "entry-1"}

    browser = BrowserClient()
    monkeypatch.setattr(
        "plexboxd.integrations.letterboxd.writer.BrowserLetterboxdClient", lambda: browser
    )
    monkeypatch.setenv("LETTERBOXD_BROWSER_FALLBACK", "true")

    provider = _StubSessionProvider(response=_log_entry_response(), clearance=False)
    writer = LetterboxdWriter(session_provider=provider)
    writer.write(
        letterboxd_film_id="27470",
        letterboxd_lid="gdKW",
        letterboxd_slug="human-traffic",
        rating=4.5,
        liked=True,
        rewatch=False,
        watched_on=date(2026, 5, 7),
    )

    assert browser.bootstrapped is True
    assert provider.calls == []


def test_browser_client_write_parses_log_entry_response(monkeypatch, tmp_path) -> None:
    cookie_file = tmp_path / "letterboxd_cookies.json"
    profile_dir = tmp_path / "profile"
    captured: dict[str, object] = {}

    monkeypatch.setenv("LETTERBOXD_USERNAME", "jan")
    monkeypatch.setenv("LETTERBOXD_PASSWORD", "secret")
    monkeypatch.setenv("LETTERBOXD_SESSION_FILE", str(cookie_file))
    monkeypatch.setenv("LETTERBOXD_BROWSER_PROFILE_DIR", str(profile_dir))
    monkeypatch.setattr(
        browser_fallback,
        "_resolve_browser_node_module",
        lambda preferred_package: (Path("/tmp/node_modules"), preferred_package),
    )
    monkeypatch.setattr(browser_fallback, "_resolve_browser_executable", lambda: None)

    log_entry = {
        "id": "fqHMA7",
        "rating": 3.0,
        "like": True,
        "diaryDetails": {"diaryDate": "2026-05-22", "rewatch": True},
        "links": [{"type": "letterboxd", "url": "https://letterboxd.com/jan/film/human-traffic/1/"}],
    }

    def fake_run(command, capture_output, text, check, env):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "command": "write",
                    "signedInAs": "jan",
                    "cookies": [{"name": "letterboxd.signed.in.as", "value": "jan", "path": "/"}],
                    "result": {
                        "ok": True,
                        "status": 200,
                        "url": "https://letterboxd.com/api/v0/production-log-entries",
                        "body": json.dumps({"logEntry": log_entry}),
                    },
                    "parsedBody": {"logEntry": log_entry},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(browser_fallback.subprocess, "run", fake_run)

    client = browser_fallback.BrowserLetterboxdClient()
    result = client.write(
        letterboxd_film_id="27470",
        letterboxd_lid="gdKW",
        letterboxd_slug="human-traffic",
        rating=3.0,
        liked=True,
        rewatch=True,
        watched_on=date(2026, 5, 22),
        tags=("plex", ""),
        review="Solid.",
    )

    assert result["write_strategy"] == "browser"
    assert result["letterboxd_entry_id"] == "fqHMA7"
    assert result["rating"] == 3.0
    assert result["rewatch"] is True
    assert result["entry_url"] == "https://letterboxd.com/jan/film/human-traffic/1/"

    command = captured["command"]
    assert "--lid" in command and "gdKW" in command
    assert json.dumps(["plex"]) in command
    assert "--review" in command


def test_browser_client_reports_missing_node_modules(monkeypatch, tmp_path) -> None:
    """A missing node_modules silently disabled this path before; the error must say so."""
    monkeypatch.setenv("LETTERBOXD_SESSION_FILE", str(tmp_path / "cookies.json"))
    monkeypatch.setattr(
        browser_fallback,
        "_resolve_browser_node_module",
        lambda preferred_package: (None, preferred_package),
    )
    monkeypatch.setattr(browser_fallback, "_resolve_browser_executable", lambda: None)

    client = browser_fallback.BrowserLetterboxdClient()
    try:
        client.verify()
    except browser_fallback.LetterboxdSessionError as exc:
        assert "npm install" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected LetterboxdSessionError")
