# src/plex_bot.py
import discord
from discord.ext import commands, tasks
from plexapi.server import PlexServer
import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler
import json
import os
import sys
from datetime import datetime
from typing import Dict, Optional
from dotenv import load_dotenv
from logging_config import DiscordHandler
from views import MovieButtons
import platform
import requests

# Set event loop policy for Windows compatibility with aiodns
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_LOGGING_WEBHOOK_URL = os.getenv("DISCORD_LOGGING_WEBHOOK_URL")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")
PLEX_TOKEN = os.getenv("PLEX_TOKEN")
PLEX_SERVER_URL = os.getenv("PLEX_SERVER_URL")
PLEX_LIBRARY_NAME = os.getenv("PLEX_LIBRARY_NAME", "Movies")
NOTIFY_CHANNEL_ID = int(os.getenv("NOTIFY_CHANNEL_ID"))
GUILD_ID = int(os.getenv("GUILD_ID"))
PLEX_USERNAME = os.getenv("PLEX_USERNAME")
PLEX_LOGO = "https://i.imgur.com/AdmDnsP.png"
LETTERBOXD_LOGO = "https://i.imgur.com/0Yd2L4i.png"

CURRENT_VERSION = "1.1.4"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MOVIE_DATA_PATH = os.path.join(SCRIPT_DIR, '../data/movie_data.json')

def setup_logging():
    """Set up logging with file, console, and Discord handlers."""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, 'plex_bot.log'),
        when='midnight',
        interval=1,
        backupCount=7,
        encoding='utf-8',
        utc=True
    )
    file_handler.setFormatter(formatter)
    file_handler.suffix = "%Y-%m-%d"
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger('PlexBot')
    logger.setLevel(logging.INFO)
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    if DISCORD_LOGGING_WEBHOOK_URL:
        discord_handler = DiscordHandler(
            webhook_url=DISCORD_LOGGING_WEBHOOK_URL,
            bot_name="Plex Bot Logging",
            title_prefix="Plex Monitor"
        )
        discord_handler.setFormatter(formatter)
        discord_handler.setLevel(logging.INFO)
        logger.addHandler(discord_handler)
    
    return logger

logger = setup_logging()

def check_latest_version():
    """Check the latest version on GitHub and compare with the current version."""
    try:
        api_url = "https://api.github.com/repos/nichtlegacy/Plexboxd/releases/latest"
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        
        latest_version = response.json().get("tag_name", "unknown")
        cleaned_latest_version = latest_version.lstrip('v')
        
        logger.info(f"Running version: v{CURRENT_VERSION} | Latest Version: v{cleaned_latest_version}")
        
        if cleaned_latest_version != CURRENT_VERSION:
            logger.warning(
                "New version available! Please update from https://github.com/nichtlegacy/Plexboxd"
            )
            
        return cleaned_latest_version
    except Exception as e:
        logger.error(f"Failed to check latest version: {str(e)}")
        return None

class PlexMonitor:
    """Monitor Plex server for watched movies."""
    def __init__(self):
        self.plex = None
        self.watched_movies = self.load_movie_data()

    async def initialize(self):
        """Initialize connection to Plex server with retries."""
        max_retries = 7
        retry_delay = 30
        for attempt in range(max_retries):
            try:
                logger.info(f"Attempting Plex connection {attempt + 1}/{max_retries}...")
                self.plex = PlexServer(PLEX_SERVER_URL, PLEX_TOKEN)
                logger.info("Plex connection established")
                return True
            except Exception as e:
                logger.error(f"Plex connection error: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
        logger.error("Failed to connect to Plex server")
        return False

    def load_movie_data(self) -> Dict:
        """Load watched movie data from JSON file."""
        if os.path.exists(MOVIE_DATA_PATH):
            with open(MOVIE_DATA_PATH, 'r') as f:
                return json.load(f)
        return {}

    def save_movie_data(self):
        """Save watched movie data to JSON file with proper error handling."""
        try:
            os.makedirs(os.path.dirname(MOVIE_DATA_PATH), exist_ok=True)
            with open(MOVIE_DATA_PATH, 'w') as f:
                json.dump(self.watched_movies, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save movie data: {str(e)}")

    def get_movie_details(self, movie) -> Optional[Dict]:
        """Extract relevant movie details from Plex."""
        logger.info(f"Extracting details for movie: {movie.title} ({movie.year})")
        try:
            hours, remaining_minutes = divmod(movie.duration // 60000, 60)
            duration_str = f"{hours}h {remaining_minutes}min" if hours else f"{remaining_minutes}min"
            return {
                'title': movie.title,
                'original_title': getattr(movie, 'originalTitle', movie.title),
                'year': movie.year if hasattr(movie, 'year') else "Unknown",
                'duration': duration_str,
                'genres': [genre.tag for genre in getattr(movie, 'genres', [])],
                'directors': [director.tag for director in getattr(movie, 'directors', [])],
                'rating': getattr(movie, 'rating', "No Rating"),
                'thumb': movie.thumbUrl if hasattr(movie, 'thumbUrl') else None,
                'ratingKey': str(movie.ratingKey),
                'last_viewed_at': movie.lastViewedAt.isoformat() if movie.lastViewedAt else None,
                'view_count': getattr(movie, 'viewCount', 0),
                'summary': getattr(movie, 'summary', "No description available")
            }
        except Exception as e:
            logger.error(f"Error fetching details for {movie.title}: {str(e)}")
            return None

    def is_movie_currently_playing(self, movie_title):
        """Check if a movie is currently being played."""
        try:
            sessions = self.plex.sessions()
            return any(
                session.title == movie_title and 
                session.type == 'movie' and 
                PLEX_USERNAME in session.usernames
                for session in sessions
            )
        except Exception:
            return False

class PlexDiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        self.plex_monitor = PlexMonitor()
        self.notify_channel = None

    async def setup_hook(self):
        """Initialize bot and Plex connection."""
        # Check version before initializing
        check_latest_version()
        
        if not await self.plex_monitor.initialize():
            logger.error("Bot startup aborted due to Plex connection failure")
            await self.close()

    async def restore_views(self):
        """Reattach views to notifications with message_id on startup."""
        logger.info("Restoring views for notifications...")
        if not self.notify_channel:
            logger.error("Notify channel not initialized, cannot restore views")
            return

        for movie_key, movie_data in self.plex_monitor.watched_movies.items():
            notification = movie_data.get('notification')
            if not notification:
                logger.debug(f"No notification data for {movie_data['title']} ({movie_data['year']}), skipping")
                continue

            message_id = notification.get('message_id')
            channel_id = notification.get('channel_id')

            if not (message_id and channel_id):
                logger.warning(f"Missing message_id or channel_id for {movie_data['title']}, skipping")
                continue

            if str(channel_id) != str(NOTIFY_CHANNEL_ID):
                logger.warning(f"Channel ID mismatch for {movie_data['title']}, skipping view restoration")
                continue

            try:
                message = await self.notify_channel.fetch_message(int(message_id))
                view = MovieButtons(
                    movie_title=movie_data['title'],
                    movie_year=movie_data['year'],
                    original_title=movie_data.get('original_title', movie_data['title']),
                    last_viewed_at=movie_data.get('last_viewed_at')
                )
                await message.edit(view=view)
                logger.info(f"Restored view for {movie_data['title']} ({movie_data['year']})")
            except discord.NotFound:
                logger.warning(f"Message {message_id} for {movie_data['title']} not found, removing notification data")
                movie_data.pop('notification', None)
                self.plex_monitor.save_movie_data()
            except Exception as e:
                logger.error(f"Failed to restore view for {movie_data['title']}: {str(e)}")

    @tasks.loop(minutes=15)
    async def check_recently_watched(self):
        """Check for recently watched movies and notify."""
        if not self.notify_channel:
            logger.error("Notify channel not initialized!")
            return
        
        try:
            recently_watched = self.plex_monitor.plex.library.section(PLEX_LIBRARY_NAME).search(
                unwatched=False,
                libtype='movie'
            )
            current_time = datetime.now()
            
            for movie in recently_watched:
                last_viewed = getattr(movie, 'lastViewedAt', None)
                if not last_viewed or (current_time - last_viewed).total_seconds() > 1800:
                    continue

                if self.plex_monitor.is_movie_currently_playing(movie.title):
                    logger.info(f"Movie {movie.title} {movie.year} is currently playing, skipping notification")
                    continue

                movie_details = self.plex_monitor.get_movie_details(movie)
                if not movie_details:
                    continue
                
                movie_key = movie_details['ratingKey']
                stored_movie = self.plex_monitor.watched_movies.get(movie_key, {})
                stored_view_count = stored_movie.get('view_count', 0)
                current_view_count = movie_details['view_count']
                stored_last_viewed = datetime.fromisoformat(stored_movie.get('last_viewed_at')) if stored_movie.get('last_viewed_at') else None

                if (movie_key not in self.plex_monitor.watched_movies or 
                    current_view_count > stored_view_count or 
                    (stored_last_viewed and (last_viewed - stored_last_viewed).total_seconds() > 7200)):
                    
                    embed, file = await self.create_movie_embed(movie_details)
                    view = MovieButtons(
                        movie_details['title'],
                        movie_details['year'],
                        movie_details['original_title'],
                        last_viewed_at=movie_details.get('last_viewed_at')
                    )
                    
                    mention = f"<@{DISCORD_USER_ID}>" if DISCORD_USER_ID else ""
                    
                    message = await self.notify_channel.send(
                        content=mention,
                        embed=embed,
                        file=file,
                        view=view
                    )
                    
                    movie_details['notification'] = {
                        'message_id': str(message.id),
                        'channel_id': str(self.notify_channel.id)
                    }
                    
                    self.plex_monitor.watched_movies[movie_key] = movie_details
                    self.plex_monitor.save_movie_data()
                    logger.info(f"Notification sent for: {movie_details['title']} ({movie_details['year']})")

        except Exception as e:
            logger.error(f"Error checking recently watched movies: {str(e)}")

    async def create_movie_embed(self, movie_details: Dict):
        """Create Discord embed for movie notification (placeholder, moved to utils)."""
        from utils import create_movie_embed
        return await create_movie_embed(movie_details)

    async def on_ready(self):
        """Handle bot startup and channel setup."""
        logger.info(f"Bot started as: {self.user}")
        self.notify_channel = self.get_channel(NOTIFY_CHANNEL_ID)
        if not self.notify_channel:
            logger.error(f"Channel {NOTIFY_CHANNEL_ID} not found!")
            await self.close()
            return
        logger.info(f"Notification channel found: #{self.notify_channel.name}")
        await self.restore_views()
        self.check_recently_watched.start()

def main():
    """Run the Plex Discord bot."""
    bot = PlexDiscordBot()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()