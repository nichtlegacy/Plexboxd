# src/utils.py
import discord
import aiohttp
import io
import logging
from typing import Dict, Tuple, Optional

# Import branding constants from main module
from discord_time import discord_timestamp, to_aware_datetime
from plex_bot import PLEX_LOGO, EMBED_AUTHOR_NAME, EMBED_FOOTER_TEXT


async def create_movie_embed(movie_details: Dict) -> Tuple[discord.Embed, Optional[discord.File]]:
    """Create a Discord embed and optional file for movie notification."""
    watched_at = to_aware_datetime(movie_details.get('last_viewed_at'))

    description = f"📜 **Description**: {shorten_summary(movie_details['summary'])}"
    watched_relative = discord_timestamp(watched_at, "R")
    if watched_relative:
        # Rendered in the description rather than the footer: embed footer text is not
        # parsed as markdown, so a <t:...> tag would show up there verbatim.
        watched_absolute = discord_timestamp(watched_at, "f")
        description = f"-# 🍿 Watched {watched_relative} · {watched_absolute}\n\n{description}"

    embed = discord.Embed(
        title=f"{movie_details['title']} ({movie_details['year']})",
        description=description,
        color=discord.Color.orange(),
        # The footer clock reflects when the film was watched, not when the bot noticed
        # it; polling runs every 15 minutes, so those are not the same moment.
        timestamp=watched_at or discord.utils.utcnow()
    )

    genres = [g.strip() for g in movie_details.get('genres', '').split(',')] if movie_details.get('genres') else ['Unknown']
    directors = [d.strip() for d in movie_details.get('directors', '').split(',')] if movie_details.get('directors') else ['Unknown']
    
    embed.add_field(name="⏳ Duration", value=movie_details['duration'], inline=True)
    embed.add_field(name="🎭 Genre", value=', '.join(genres[:3]), inline=True)
    embed.add_field(name="🎬 Director", value=', '.join(directors), inline=True)
    embed.add_field(name="⭐ Rating", value=movie_details['rating'], inline=True)
    
    if movie_details.get('library'):
        embed.add_field(name="📚 Library", value=movie_details['library'], inline=True)
    else:
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
    
    # If rewatch: View Count in same row as Library, Last Viewed (previous date) in next row
    if movie_details['view_count'] > 1 and movie_details.get('previous_viewed_at'):
        embed.add_field(name="📊 View Count", value=str(movie_details['view_count']), inline=True)
        # A dynamic timestamp instead of a fixed d.m.Y string: field values do render
        # markdown, so each viewer sees this in their own timezone and format.
        previous_viewed = discord_timestamp(movie_details['previous_viewed_at'], "R")
        embed.add_field(
            name="👀 Last Viewed",
            value=previous_viewed or "Unknown",
            inline=True
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
    else:
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
    
    embed.set_author(name=EMBED_AUTHOR_NAME, icon_url=PLEX_LOGO)
    embed.set_thumbnail(url=PLEX_LOGO)
    # Footer text stays plain: it is not parsed as markdown. Its clock now carries the
    # watch time set above, so "Watched" and the rendered time finally agree.
    embed.set_footer(text=EMBED_FOOTER_TEXT, icon_url=PLEX_LOGO)
    
    file = None
    if movie_details.get('thumb'):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(movie_details['thumb']) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        file = discord.File(io.BytesIO(data), filename="movie_poster.jpg")
                        embed.set_image(url="attachment://movie_poster.jpg")
        except Exception as e:
            logging.getLogger('PlexBot').error(f"Failed to load movie poster: {str(e)}")

    return embed, file

def shorten_summary(summary: str, min_length: int = 300, max_length: int = 400) -> str:
    """Shorten summary to end at a period between min_length and max_length."""
    if len(summary) <= max_length:
        return summary.strip()
    
    segment = summary[min_length:max_length]
    last_period = segment.rfind('.')
    
    if last_period != -1:
        return summary[:min_length + last_period + 1].strip()
    
    last_period_before = summary[:min_length].rfind('.')
    if last_period_before != -1:
        return summary[:last_period_before + 1].strip()
    
    return summary[:max_length].strip()