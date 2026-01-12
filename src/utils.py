# src/utils.py
import discord
import aiohttp
import io
import logging
from datetime import datetime
from typing import Dict, Tuple, Optional
import json

# Import branding constants from main module
from plex_bot import PLEX_LOGO, LETTERBOXD_LOGO, EMBED_AUTHOR_NAME, EMBED_FOOTER_TEXT

async def create_movie_embed(movie_details: Dict) -> Tuple[discord.Embed, Optional[discord.File]]:
    """Create a Discord embed and optional file for movie notification."""
    embed = discord.Embed(
        title=f"{movie_details['title']} ({movie_details['year']})",
        description=f"📜 **Description**: {shorten_summary(movie_details['summary'])}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
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
        embed.add_field(
            name="👀 Last Viewed",
            value=datetime.fromisoformat(movie_details['previous_viewed_at']).strftime('%d.%m.%Y %H:%M'),
            inline=True
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
    else:
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for alignment
    
    embed.set_author(name=EMBED_AUTHOR_NAME, icon_url=PLEX_LOGO)
    embed.set_thumbnail(url=PLEX_LOGO)
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