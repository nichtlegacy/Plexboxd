from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from shutil import which

from plexboxd.infrastructure.paths import project_root, resolve_data_path

from .session import LetterboxdSessionError, cookie_file

logger = logging.getLogger("LetterboxdIntegration")


class BrowserLetterboxdClient:
    def __init__(self) -> None:
        # Must resolve the same variable as session.cookie_file(): the browser writes the
        # bundle the HTTP client reads, and a mismatch silently forces every write through
        # the browser.
        self.cookie_file = cookie_file()
        self.script_path = Path(__file__).with_name("scripts") / "letterboxd_browser.cjs"
        # Headless Chrome gets challenged by Cloudflare on letterboxd.com where headful
        # does not, so default to headful and rely on xvfb-run when there is no display.
        self.headless = _env_bool("LETTERBOXD_BROWSER_HEADLESS", False)
        if self.headless:
            logger.warning(
                "LETTERBOXD_BROWSER_HEADLESS is enabled; Cloudflare challenges headless "
                "Chrome on letterboxd.com, so login will likely fail. Leave it unset."
            )
        self.profile_dir = resolve_data_path(
            os.getenv("LETTERBOXD_BROWSER_PROFILE_DIR", "data/letterboxd-browser-profile")
        )
        preferred_package = (os.getenv("LETTERBOXD_BROWSER_PACKAGE", "patchright").strip() or "patchright").lower()
        self.node_path, self.browser_package = _resolve_browser_node_module(preferred_package)
        self.executable_path = _resolve_browser_executable()
        self.browser_channel = os.getenv("LETTERBOXD_BROWSER_CHANNEL") or _resolve_browser_channel(self.executable_path)
        self.username = os.getenv("LETTERBOXD_USERNAME")
        self.password = os.getenv("LETTERBOXD_PASSWORD")

    def verify(self) -> dict:
        payload = self._run_browser_command("verify")
        self._persist_cookies(payload)
        return payload

    def bootstrap(self) -> dict:
        payload = self._run_browser_command(
            "bootstrap",
            extra_args=[
                "--username",
                self._require_credential(self.username, "LETTERBOXD_USERNAME"),
                "--password",
                self._require_credential(self.password, "LETTERBOXD_PASSWORD"),
            ],
        )
        self._persist_cookies(payload)
        return payload

    def write(
        self,
        *,
        letterboxd_film_id: str,
        letterboxd_slug: str,
        rating: float,
        liked: bool,
        rewatch: bool,
        watched_on,
        letterboxd_lid: str | None = None,
        tags: tuple[str, ...] | list[str] = (),
        review: str = "",
    ) -> dict:
        extra_args = [
            "--film-id",
            str(letterboxd_film_id),
            "--slug",
            str(letterboxd_slug),
            "--rating",
            str(rating),
            "--liked",
            "true" if liked else "false",
            "--rewatch",
            "true" if rewatch else "false",
            "--watched-on",
            watched_on.isoformat(),
            "--tags",
            json.dumps([tag.strip() for tag in tags if tag and tag.strip()]),
            "--username",
            self._require_credential(self.username, "LETTERBOXD_USERNAME"),
            "--password",
            self._require_credential(self.password, "LETTERBOXD_PASSWORD"),
        ]
        if letterboxd_lid:
            extra_args.extend(["--lid", str(letterboxd_lid)])
        if review and review.strip():
            extra_args.extend(["--review", review.strip()])

        payload = self._run_browser_command("write", extra_args=extra_args)
        self._persist_cookies(payload)

        result = payload.get("result", {})
        parsed_body = payload.get("parsedBody")
        if result.get("ok") is not True:
            raise LetterboxdSessionError(
                f"Browser write rejected: status={result.get('status')} url={result.get('url')}"
            )

        log_entry = parsed_body.get("logEntry") if isinstance(parsed_body, dict) else None
        if not isinstance(log_entry, dict):
            raise LetterboxdSessionError(
                f"Browser write returned non-success payload: {parsed_body or result.get('body')}"
            )

        diary_details = log_entry.get("diaryDetails") or {}
        return {
            "write_strategy": "browser",
            "letterboxd_entry_id": log_entry.get("id"),
            "watched_on": watched_on.isoformat(),
            "rating": log_entry.get("rating"),
            "liked": log_entry.get("like"),
            "rewatch": diary_details.get("rewatch"),
            "diary_date": diary_details.get("diaryDate"),
            "entry_url": _first_entry_url(log_entry),
            "response": log_entry,
        }

    def _run_browser_command(self, command_name: str, *, extra_args: list[str] | None = None) -> dict:
        if self.node_path is None:
            raise LetterboxdSessionError(
                "Browser automation package not found: no node_modules containing "
                f"'{self.browser_package}'. Run `npm install` in the project root "
                "(or set LETTERBOXD_BROWSER_PACKAGE)."
            )

        command = self._build_command(command_name, extra_args=extra_args or [])
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "NODE_PATH": str(self.node_path),
                "LETTERBOXD_BROWSER_PACKAGE": self.browser_package,
            },
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown browser automation failure"
            raise LetterboxdSessionError(stderr)

        stdout = completed.stdout.strip()
        if not stdout:
            raise LetterboxdSessionError("Browser automation returned no output")

        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise LetterboxdSessionError("Browser automation returned invalid JSON") from exc

    def _build_command(self, command_name: str, *, extra_args: list[str]) -> list[str]:
        command = [
            "node",
            str(self.script_path),
            "--command",
            command_name,
            "--profile-dir",
            str(self.profile_dir),
            "--headless",
            "true" if self.headless else "false",
        ]
        if self.cookie_file.exists():
            command.extend(["--cookie-file", str(self.cookie_file)])
        if self.browser_channel:
            command.extend(["--browser-channel", self.browser_channel])
        elif self.executable_path is not None:
            command.extend(["--executable-path", str(self.executable_path)])
        command.extend(extra_args)
        if not self.headless and not os.getenv("DISPLAY") and which("xvfb-run"):
            return ["xvfb-run", "-a", *command]
        return command

    def _persist_cookies(self, payload: dict) -> None:
        cookies = payload.get("cookies")
        if not isinstance(cookies, list):
            return

        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        with self.cookie_file.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "cookies": cookies,
                    "saved_at": int(time.time()),
                    "signed_in_as": payload.get("signedInAs"),
                },
                handle,
                indent=2,
            )

    @staticmethod
    def _require_credential(value: str | None, env_name: str) -> str:
        if value:
            return value
        raise LetterboxdSessionError(f"{env_name} missing")


PlaywrightLetterboxdWriter = BrowserLetterboxdClient


def _first_entry_url(log_entry: dict) -> str | None:
    for link in log_entry.get("links") or []:
        if isinstance(link, dict) and link.get("url"):
            return str(link["url"])
    return None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_browser_node_module(preferred_package: str) -> tuple[Path | None, str]:
    package_order = [preferred_package]
    if preferred_package != "patchright":
        package_order.append("patchright")
    if "playwright" not in package_order:
        package_order.append("playwright")

    # In Docker the working directory is /app/src while node_modules lives at /app, so
    # anchor on the project root rather than relying on cwd.
    local_search_roots = [
        project_root() / "node_modules",
        *(parent / "node_modules" for parent in Path(__file__).resolve().parents[:6]),
        Path.cwd() / "node_modules",
    ]

    for package_name in package_order:
        for node_modules_dir in local_search_roots:
            candidate = node_modules_dir / package_name / "index.js"
            if candidate.exists():
                return candidate.parent.parent, package_name

    npm_root = Path.home() / ".npm" / "_npx"
    if not npm_root.exists():
        return None, preferred_package

    for package_name in package_order:
        candidates = sorted(
            npm_root.glob(f"*/node_modules/{package_name}/index.js"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0].parent.parent, package_name
    return None, preferred_package


def _resolve_browser_executable() -> Path | None:
    candidates = [
        os.getenv("CHROME_BIN"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/opt/homebrew/bin/chromium",
        # Linux / Docker
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _resolve_browser_channel(executable_path: Path | None) -> str | None:
    if executable_path is None:
        return None
    executable = str(executable_path)
    if "Google Chrome.app" in executable or executable.endswith("/Google Chrome"):
        return "chrome"
    if "Google Chrome Canary.app" in executable or executable.endswith("/Google Chrome Canary"):
        return "chrome-canary"
    return None
