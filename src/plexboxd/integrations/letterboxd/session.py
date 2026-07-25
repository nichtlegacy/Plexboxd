from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests as curl_requests

from plexboxd.infrastructure.paths import resolve_data_path


BASE_URL = "https://letterboxd.com"
SIGN_IN_URL = f"{BASE_URL}/sign-in/"
LOGIN_URL = f"{BASE_URL}/user/login.do"
SETTINGS_URL = f"{BASE_URL}/settings/"
ACTIVITY_URL = f"{BASE_URL}/activity/"
CSRF_COOKIE = "com.xk72.webparts.csrf"
SIGNED_IN_COOKIE = "letterboxd.signed.in.as"
CLEARANCE_COOKIE = "cf_clearance"
DEFAULT_COOKIE_FILE = "data/letterboxd_cookies.json"

# Only profiles verified to pass Cloudflare on both GET and POST against letterboxd.com.
# Measured: chrome120 -> GET 200 but POST 403 (cf-mitigated: challenge);
#           chrome (alias of chrome142) -> GET 403. Both were previously the defaults.
DEFAULT_IMPERSONATION_PROFILES = ("chrome136", "firefox135", "safari184")

# Header set must stay consistent with the impersonated TLS fingerprint, otherwise
# Cloudflare challenges the request even though the fingerprint itself is fine.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
CLIENT_HINT_HEADERS = {
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


class LetterboxdSessionError(RuntimeError):
    pass


class CloudflareChallengeError(LetterboxdSessionError):
    pass


class AuthenticationError(LetterboxdSessionError):
    pass


class CsrfTokenError(LetterboxdSessionError):
    """Raised when Letterboxd rejects the CSRF token so the caller can refresh it."""


@dataclass(slots=True, frozen=True)
class AuthenticatedSession:
    session: curl_requests.Session
    csrf_token: str
    username: str | None


class LetterboxdSessionProvider:
    def __init__(self) -> None:
        self.username = os.getenv("LETTERBOXD_USERNAME")
        self.password = os.getenv("LETTERBOXD_PASSWORD")
        self.timeout_seconds = int(os.getenv("LETTERBOXD_TIMEOUT_SECONDS", "20"))
        self.max_retries = int(os.getenv("LETTERBOXD_MAX_RETRIES", "3"))
        self.base_backoff_seconds = float(os.getenv("LETTERBOXD_BASE_BACKOFF_SECONDS", "5.0"))
        self.impersonation_profiles = _configured_impersonation_profiles()
        self.browser_auth_enabled = os.getenv("LETTERBOXD_BROWSER_AUTH", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @contextmanager
    def open(self, *, force_login: bool = False):
        session = self.ensure_session(force_login=force_login)
        try:
            yield AuthenticatedSession(
                session=session,
                csrf_token=self._require_csrf(session),
                username=session.cookies.get(SIGNED_IN_COOKIE),
            )
        finally:
            session.close()

    def verify(self) -> None:
        if self.browser_auth_enabled:
            self._browser_client().verify()
            return None
        with self.open():
            return None

    def bootstrap(self) -> None:
        if self.browser_auth_enabled:
            self._browser_client().bootstrap()
            return None
        with self.open(force_login=True):
            return None

    @contextmanager
    def open_public(self):
        session = self._load_persisted_session() or self._create_session()
        try:
            yield session
        finally:
            session.close()

    def ensure_session(self, *, force_login: bool = False) -> curl_requests.Session:
        if not force_login:
            persisted_session = self._load_persisted_session()
            if persisted_session is not None and self._is_session_valid(persisted_session):
                return persisted_session
            if persisted_session is not None:
                persisted_session.close()

        if self.browser_auth_enabled:
            self._browser_client().bootstrap()
            persisted_session = self._load_persisted_session()
            if persisted_session is None:
                raise AuthenticationError("Browser login did not persist a reusable Letterboxd session")
            if self._is_session_valid(persisted_session):
                return persisted_session
            persisted_session.close()
            raise AuthenticationError("Browser login persisted a guest or invalid Letterboxd session")

        session = self._create_session()
        self._login(session)
        self._save_session(session)
        return session

    def get(self, url: str, *, session: curl_requests.Session, allow_redirects: bool = True, headers: dict | None = None):
        return self._request(
            session,
            "GET",
            url,
            allow_redirects=allow_redirects,
            headers=headers,
        )

    def post(
        self,
        url: str,
        *,
        session: curl_requests.Session,
        data: dict | str,
        allow_redirects: bool = True,
        headers: dict | None = None,
    ):
        return self._request(
            session,
            "POST",
            url,
            data=data,
            allow_redirects=allow_redirects,
            headers=headers,
        )

    def api_post(
        self,
        path: str,
        *,
        session: curl_requests.Session,
        json_body: dict,
        referer: str,
    ):
        """POST JSON to Letterboxd's /api/v0 endpoints.

        The API authenticates via cookies and requires the CSRF cookie value to be
        echoed back in the ``x-csrf-token`` header. On rejection the token is
        refreshed once from a page load, because the cookie rotates server-side.
        """
        url = path if path.startswith("http") else f"{BASE_URL}{path}"

        for attempt in (1, 2):
            headers = {
                "accept": "*/*",
                "content-type": "application/json; charset=UTF-8",
                "origin": BASE_URL,
                "referer": referer,
                "x-csrf-token": self._require_csrf(session),
                **CLIENT_HINT_HEADERS,
            }
            response = self._request(
                session,
                "POST",
                url,
                data=json.dumps(json_body),
                headers=headers,
                allow_redirects=False,
            )
            if not _is_csrf_rejection(response):
                return response
            if attempt == 2:
                raise CsrfTokenError(f"Letterboxd rejected the CSRF token for {url}")
            self._refresh_csrf(session, referer)

        raise CsrfTokenError(f"Letterboxd rejected the CSRF token for {url}")

    def _refresh_csrf(self, session: curl_requests.Session, referer: str) -> None:
        """Reload a page so Letterboxd reissues the CSRF cookie."""
        self.get(referer, session=session, allow_redirects=True)

    def _create_session(self) -> curl_requests.Session:
        return curl_requests.Session(
            impersonate=self.impersonation_profiles[0],
            headers={
                "referer": BASE_URL,
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9",
                "user-agent": BROWSER_USER_AGENT,
                **CLIENT_HINT_HEADERS,
            },
        )

    def _load_persisted_session(self) -> curl_requests.Session | None:
        payload = self._load_cookie_payload()
        if payload is None:
            return None

        session = self._create_session()
        now = time.time()
        for cookie in payload:
            # Skip expired cookies rather than replaying them; a stale
            # letterboxd.signed.in.as makes an otherwise good session look logged out.
            expires = cookie.get("expires")
            try:
                if expires is not None and float(expires) > 0 and float(expires) <= now:
                    continue
            except (TypeError, ValueError):
                pass

            set_kwargs: dict[str, object] = {
                "name": cookie["name"],
                "value": cookie["value"],
                "path": cookie.get("path", "/"),
                "secure": bool(cookie.get("secure", False)),
            }
            if cookie.get("domain"):
                set_kwargs["domain"] = cookie["domain"]
            session.cookies.set(**set_kwargs)
        return session

    def _load_cookie_payload(self) -> list[dict] | None:
        path = cookie_file()
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, TypeError):
            return None

        if isinstance(payload, dict) and "cookies" in payload:
            payload = payload["cookies"]
        return payload if isinstance(payload, list) else None

    def _persist_cookie_payload(self, cookies: list[dict]) -> None:
        path = cookie_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump({"cookies": cookies, "saved_at": int(time.time())}, handle, indent=2)

    def _save_session(self, session: curl_requests.Session) -> None:
        cookies = []
        for cookie in session.cookies.jar:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": bool(cookie.secure),
                    "expires": cookie.expires,
                }
            )
        self._persist_cookie_payload(cookies)

    @staticmethod
    def has_clearance(session: curl_requests.Session) -> bool:
        """Whether the session carries a Cloudflare clearance cookie.

        Writes to /api/v0 need one, and only a real browser can mint it.
        """
        return bool(session.cookies.get(CLEARANCE_COOKIE))

    def _is_session_valid(self, session: curl_requests.Session) -> bool:
        if not session.cookies.get(SIGNED_IN_COOKIE):
            return False

        try:
            response = self.get(SETTINGS_URL, session=session, allow_redirects=True)
        except LetterboxdSessionError:
            return False

        final_url = str(response.url)
        if "/sign-in/" in final_url:
            return False
        if self._is_cloudflare_response(response):
            return False
        if self._looks_logged_out(response.text):
            return False
        return response.status_code == 200 and bool(session.cookies.get(SIGNED_IN_COOKIE))

    def _login(self, session: curl_requests.Session) -> None:
        if not self.username or not self.password:
            raise AuthenticationError("LETTERBOXD_USERNAME or LETTERBOXD_PASSWORD missing")

        self.get(SIGN_IN_URL, session=session, allow_redirects=True)
        csrf_token = self._require_csrf(session)
        response = self.post(
            LOGIN_URL,
            session=session,
            data={
                "__csrf": csrf_token,
                "username": self.username,
                "password": self.password,
                "remember": "true",
            },
            allow_redirects=True,
            headers={
                "Origin": BASE_URL,
                "Referer": SIGN_IN_URL,
            },
        )

        if self._is_cloudflare_response(response):
            raise CloudflareChallengeError("Letterboxd login blocked by Cloudflare challenge")

        if not session.cookies.get(SIGNED_IN_COOKIE):
            raise AuthenticationError(f"Letterboxd login did not produce a signed-in session (final_url={response.url})")

        validation = self.get(ACTIVITY_URL, session=session, allow_redirects=True)
        if self._is_cloudflare_response(validation):
            raise CloudflareChallengeError("Authenticated Letterboxd session is blocked by Cloudflare challenge")
        if self._looks_logged_out(validation.text):
            raise AuthenticationError("Letterboxd login returned a guest session")

    def _require_csrf(self, session: curl_requests.Session) -> str:
        token = session.cookies.get(CSRF_COOKIE)
        if not token:
            raise LetterboxdSessionError("Letterboxd session missing CSRF cookie")
        return token

    def _request(self, session: curl_requests.Session, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout_seconds)
        last_exception = None
        last_response = None

        for attempt in range(self.max_retries):
            for profile in self.impersonation_profiles:
                try:
                    response = session.request(method, url, impersonate=profile, **kwargs)
                except curl_requests.errors.RequestsError as exc:  # pragma: no cover - network path
                    last_exception = exc
                    continue

                last_response = response
                if self._is_cloudflare_response(response):
                    continue
                return response

            if attempt < self.max_retries - 1:
                # Exponential backoff: every profile was challenged this round, so retrying
                # immediately just burns through the rate limit.
                time.sleep(self.base_backoff_seconds * (2 ** attempt))

        if last_response is not None and self._is_cloudflare_response(last_response):
            raise CloudflareChallengeError(
                f"Letterboxd request blocked by Cloudflare: method={method} url={url} status={last_response.status_code}"
            )
        if last_exception is not None:
            raise LetterboxdSessionError(f"Letterboxd request failed: {method} {url}: {last_exception}") from last_exception
        raise LetterboxdSessionError(f"Letterboxd request failed without response: {method} {url}")

    @staticmethod
    def _is_cloudflare_response(response) -> bool:
        return _is_cloudflare_response(response)

    @staticmethod
    def _looks_logged_out(text: str) -> bool:
        """Detect a guest page from server-rendered markup.

        Two traps here:
        * letterboxd.com ships a static analytics stub containing ``loggedIn: false``
          and ``role: "guest"`` even to authenticated members.
        * the ``logged-in`` body class is added client-side, so it is absent from plain
          HTTP responses even when the session is valid.

        When a guest requests a members-only page the server returns 200 with a
        sign-in flow body class instead of redirecting, so that class is the signal.
        """
        lowered = (text or "").lower()
        return "screen-standalone-flow-sign-in" in lowered or 'id="sign-in-form"' in lowered

    @staticmethod
    def _browser_client():
        from .browser_fallback import BrowserLetterboxdClient

        return BrowserLetterboxdClient()


def cookie_file() -> Path:
    """Path of the persisted cookie bundle.

    Resolved per call rather than at import time: ``load_dotenv()`` runs during
    container bootstrap, so a module-level constant would capture the value from
    before the .env file was read. BrowserLetterboxdClient resolves the same variable
    when it writes, and the two must agree or the browser stores cookies the HTTP
    client never finds.
    """
    return resolve_data_path(os.getenv("LETTERBOXD_SESSION_FILE", DEFAULT_COOKIE_FILE))


def _configured_impersonation_profiles() -> tuple[str, ...]:
    configured = os.getenv("LETTERBOXD_IMPERSONATE", "").strip()
    if not configured:
        return DEFAULT_IMPERSONATION_PROFILES
    profiles = tuple(part.strip() for part in configured.split(",") if part.strip())
    return profiles or DEFAULT_IMPERSONATION_PROFILES


def _is_cloudflare_response(response) -> bool:
    """Detect a Cloudflare interstitial.

    The authoritative signal is the ``cf-mitigated`` response header. Body sniffing
    alone misclassifies legitimate JSON 403s (for example ``Invalid CSRF token``) as
    challenges, which would send the caller into a pointless profile rotation.
    """
    headers = getattr(response, "headers", None) or {}
    try:
        mitigated = headers.get("cf-mitigated") or headers.get("Cf-Mitigated")
    except AttributeError:  # pragma: no cover - defensive for exotic header containers
        mitigated = None
    if mitigated and str(mitigated).strip().lower() == "challenge":
        return True

    text = (getattr(response, "text", "") or "").lower()
    if _is_csrf_rejection(response):
        return False
    return (
        ("just a moment" in text and "cloudflare" in text)
        or "cf_chl_" in text
        or ("attention required" in text and "cloudflare" in text)
        or ("access denied" in text and "cloudflare" in text)
        or "error1015" in text
        or "you are being rate limited" in text
    )


def _is_csrf_rejection(response) -> bool:
    if getattr(response, "status_code", None) != 403:
        return False
    text = (getattr(response, "text", "") or "").strip().lower()
    return "invalid csrf token" in text
