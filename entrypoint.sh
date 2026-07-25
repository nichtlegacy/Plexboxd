#!/bin/bash
set -euo pipefail

# The Letterboxd login needs a headful Chromium (Cloudflare challenges headless), so a
# virtual display has to exist before the bot starts. Chromium is launched by Patchright
# as a child of the Python process and connects to this display.
DISPLAY_NUM="${PLEXBOXD_DISPLAY_NUM:-99}"
export DISPLAY=":${DISPLAY_NUM}"

Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!

cleanup() {
    kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Poll for readiness instead of a fixed sleep: on a loaded host Xvfb can take longer than
# a second, and starting Chromium against a display that is not up yet fails the login.
for _ in $(seq 1 50); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "entrypoint: Xvfb exited during startup; the Letterboxd browser login will not work" >&2
        break
    fi
    sleep 0.2
done

if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    # Not fatal: ratings are written over HTTP and only the login needs the browser, so
    # start anyway and let that step report the problem.
    echo "entrypoint: display $DISPLAY not ready; continuing without it" >&2
fi

exec python -u plex_bot.py
