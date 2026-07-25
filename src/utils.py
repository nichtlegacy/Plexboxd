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

    embed = discord.Embed(
        title=f"{movie_details['title']} ({movie_details['year']})",
        description=f"📜 **Description**: {shorten_summary(movie_details['summary'])}",
        color=discord.Color.orange(),
        # Discord renders this next to the footer text in each viewer's own timezone and
        # locale. It has to be the watch time, not utcnow(): polling runs every 15
        # minutes, so "Watched" and the rendered clock were not the same moment.
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
        # Dynamic timestamps instead of a fixed d.m.Y string: field values do render
        # markdown, so each viewer sees this in their own timezone and locale. The date
        # answers "when did I last see this", the relative line how long ago that was.
        previous_date = discord_timestamp(movie_details['previous_viewed_at'], "D")
        previous_relative = discord_timestamp(movie_details['previous_viewed_at'], "R")
        embed.add_field(
            name="👀 Last Viewed",
            value=f"{previous_date}\n-# {previous_relative}" if previous_date else "Unknown",
            inline=True
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
    else:
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
    
    embed.set_author(name=EMBED_AUTHOR_NAME, icon_url=PLEX_LOGO)
    embed.set_thumbnail(url=PLEX_LOGO)
    # Just the label: Discord appends the embed timestamp after it, so spelling out a
    # date here would duplicate it. Footer text is not parsed as markdown either, so a
    # <t:...> tag would render verbatim.
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