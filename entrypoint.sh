#!/bin/bash
# Start Xvfb in background
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
export DISPLAY=:99

# Wait for Xvfb to start
sleep 1

# Run Python with unbuffered output
exec python -u plex_bot.py