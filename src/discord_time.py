# src/discord_time.py
"""Helpers for Discord's dynamic timestamp markup.

Kept free of project imports: both plex_bot and views need these, and utils already
imports plex_bot, so putting them there would create an import cycle.
"""
from datetime import datetime
from typing import Optional


def to_aware_datetime(value) -> Optional[datetime]:
    """Coerce a Plex timestamp into an aware datetime.

    plexapi builds its datetimes with ``datetime.fromtimestamp()`` and no timezone, so
    they are naive values in the host's local time. Attaching the local offset keeps the
    instant correct once it is converted to a Unix timestamp for Discord.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.astimezone()
    return value


def discord_timestamp(value, style: str = "R") -> Optional[str]:
    """Render a Discord dynamic timestamp such as ``<t:1784000000:R>``.

    Discord renders these in each viewer's own timezone and locale, which a
    pre-formatted string cannot do. Style ``R`` is relative ("2 hours ago"), ``f`` is a
    short absolute date and time.

    Note this markup only renders where Discord parses markdown: embed descriptions and
    field values do, but embed footer text and button labels do not.
    """
    aware = to_aware_datetime(value)
    if aware is None:
        return None
    return f"<t:{int(aware.timestamp())}:{style}>"
