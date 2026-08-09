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
import signal
import sqlite3
from contextlib import contextmanager
from uuid import uuid5, NAMESPACE_URL

from plexboxd.domain.models import NotificationRecord, WatchEvent
from plexboxd.infrastructure.bootstrap import build_application_container
from plexboxd.infrastructure.paths import resolve_data_path
from plexboxd.infrastructure.queue.worker import RatingJobWorker

# Set event loop policy for Windows compatibility with aiodns
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Load environment variables
load_dotenv()

def _required_env(name: str) -> str:
    """Read a mandatory setting, failing with a message that names the variable."""
    value = os.getenv(name)
    if not value or not value.strip():
        raise SystemExit(
            f"Configuration error: {name} is not set. "
            "Check your .env file (see .env.example) or the container environment."
        )
    return value.strip()


def _required_int_env(name: str) -> int:
    value = _required_env(name)
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"Configuration error: {name} must be a number, got {value!r}.") from None


DISCORD_TOKEN = _required_env("DISCORD_TOKEN")
DISCORD_LOGGING_WEBHOOK_URL = os.getenv("DISCORD_LOGGING_WEBHOOK_URL")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")
PLEX_TOKEN = _required_env("PLEX_TOKEN")
PLEX_SERVER_URL = _required_env("PLEX_SERVER_URL")
NOTIFY_CHANNEL_ID = _required_int_env("NOTIFY_CHANNEL_ID")
GUILD_ID = _required_int_env("GUILD_ID")
PLEX_USERNAME = _required_env("PLEX_USERNAME")
EXCLUDED_LIBRARIES = [lib.strip() for lib in os.getenv("EXCLUDED_LIBRARIES", "").split(",") if lib.strip()]

# Global branding constants
PLEX_LOGO = "https://i.imgur.com/AdmDnsP.png"
LETTERBOXD_LOGO = "https://i.imgur.com/0Yd2L4i.png"
EMBED_AUTHOR_NAME = "Plex Movie Notification 🎬"
EMBED_FOOTER_TEXT = "Watched"

CURRENT_VERSION = "1.3.3"

# Anchored to the project root, so these stay correct whatever the working directory is.
# Pre-1.1 installations stored watch history in the JSON file; it is migrated into
# movies.db on startup and then left alone.
LEGACY_MOVIE_JSON_PATH = str(resolve_data_path('data/movie_data.json'))
MOVIE_DB_PATH = str(resolve_data_path('data/movies.db'))
RATING_DB_PATH = str(resolve_data_path('data/plexboxd.db'))

def setup_logging():
    """Configure the bot and Letterboxd loggers with file, console and Discord handlers.

    The log directory is anchored to the project root rather than the working directory:
    the bot runs from src/ (and Docker sets WORKDIR /app/src), so a relative "logs" path
    wrote to /app/src/logs instead of the mounted /app/logs volume.
    """
    log_dir = resolve_data_path(os.getenv("PLEXBOXD_LOG_DIR", "logs"))
    os.makedirs(log_dir, exist_ok=True)

    # Timestamps follow the container's local time so they match the operator's wall
    # clock. Set TZ (e.g. TZ=Europe/Berlin) to pick that zone; without it Docker images
    # default to UTC, which made log lines look hours off.
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    def _file_handler(filename: str) -> TimedRotatingFileHandler:
        handler = TimedRotatingFileHandler(
            filename=os.path.join(log_dir, filename),
            when='midnight',
            interval=1,
            backupCount=7,
            encoding='utf-8',
            # Rotate on the same clock the timestamps use, otherwise the file would roll
            # over in the middle of the logged day.
            utc=False
        )
        handler.setFormatter(formatter)
        handler.suffix = "%Y-%m-%d"
        return handler

    def _discord_handler(bot_name: str, title_prefix: str):
        handler = DiscordHandler(
            webhook_url=DISCORD_LOGGING_WEBHOOK_URL,
            bot_name=bot_name,
            title_prefix=title_prefix
        )
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        return handler

    logger = logging.getLogger('PlexBot')
    logger.setLevel(logging.INFO)
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.addHandler(_file_handler('plex_bot.log'))
    logger.addHandler(console_handler)
    if DISCORD_LOGGING_WEBHOOK_URL:
        logger.addHandler(_discord_handler("Plex Bot Logging", "Plex Monitor"))

    # The Letterboxd integration modules log to their own logger. Without handlers here it
    # fell through to logging's last-resort stderr writer, so diary write failures were
    # never recorded in any file.
    letterboxd_logger = logging.getLogger('LetterboxdIntegration')
    letterboxd_logger.setLevel(logging.INFO)
    if letterboxd_logger.hasHandlers():
        letterboxd_logger.handlers.clear()
    letterboxd_logger.addHandler(_file_handler('letterboxd_integration.log'))
    letterboxd_logger.addHandler(console_handler)
    if DISCORD_LOGGING_WEBHOOK_URL:
        letterboxd_logger.addHandler(_discord_handler("Letterboxd Bot Logging", "Letterboxd Integration"))
    # Handlers are attached here, so do not also bubble up to the root logger.
    letterboxd_logger.propagate = False

    return logger

logger = setup_logging()

def _version_tuple(version: str) -> tuple:
    """Parse a version for ordered comparison.

    Comparing version strings directly is wrong once a component reaches two digits
    ("1.10.0" < "1.3.0" lexically), which would silently stop the update notice.
    """
    parts = []
    for piece in (version or "").split("."):
        digits = "".join(char for char in piece if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_latest_version():
    """Check the latest version on GitHub and compare with the current version."""
    try:
        api_url = "https://api.github.com/repos/nichtlegacy/Plexboxd/releases/latest"
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()

        latest_version = response.json().get("tag_name", "unknown")
        cleaned_latest_version = latest_version.lstrip('v')

        logger.info(f"Running version: v{CURRENT_VERSION} | Latest Version: v{cleaned_latest_version}")

        if _version_tuple(cleaned_latest_version) > _version_tuple(CURRENT_VERSION):
            logger.warning(
                "New version available! Please update from https://github.com/nichtlegacy/Plexboxd"
            )
            
        return cleaned_latest_version
    except Exception as e:
        logger.error(f"Failed to check latest version: {str(e)}")
        return None

class MovieDatabase:
    """SQLite database handler for movie data."""
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the database with required tables."""
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS movies (
                    rating_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    original_title TEXT,
                    year INTEGER,
                    duration TEXT,
                    genres TEXT,
                    directors TEXT,
                    rating REAL,
                    thumb TEXT,
                    last_viewed_at TEXT,
                    view_count INTEGER DEFAULT 0,
                    summary TEXT,
                    tmdb_id TEXT,
                    is_rated BOOLEAN DEFAULT 0,
                    notification_data TEXT
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_last_viewed ON movies(last_viewed_at)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_is_rated ON movies(is_rated)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_tmdb_id ON movies(tmdb_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_title_year ON movies(title, year)')

    @contextmanager
    def _get_connection(self):
        """Yield a connection that commits on success and rolls back on failure.

        Committing here rather than at each call site: several writers (mark_as_rated,
        the schema setup, the notification-state updates) relied on an implicit commit
        that never happened, so their changes were discarded when the connection closed.
        Callers may still commit explicitly; a second commit is a no-op.
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database error: {str(e)}")
            raise
        finally:
            if conn:
                conn.close()

    def save_movie(self, movie_data):
        """Save or update movie data in the database."""
        with self._get_connection() as conn:
            notification_data = movie_data.get('notification_data', '{}')
            if not isinstance(notification_data, str):
                notification_data = json.dumps(notification_data)

            conn.execute('''
                INSERT OR REPLACE INTO movies (
                    rating_key, title, original_title, year, duration,
                    genres, directors, rating, thumb, last_viewed_at,
                    view_count, summary, tmdb_id, is_rated, notification_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                movie_data['ratingKey'],
                movie_data['title'],
                movie_data.get('original_title'),
                movie_data.get('year'),
                movie_data.get('duration'),
                json.dumps(movie_data.get('genres', [])),
                json.dumps(movie_data.get('directors', [])),
                movie_data.get('rating'),
                movie_data.get('thumb'),
                movie_data.get('last_viewed_at'),
                movie_data.get('view_count', 0),
                movie_data.get('summary'),
                movie_data.get('tmdb_id'),
                movie_data.get('is_rated', False),
                notification_data
            ))
            conn.commit()

    def get_movie(self, rating_key):
        """Get movie data from the database."""
        with self._get_connection() as conn:
            cursor = conn.execute('SELECT * FROM movies WHERE rating_key = ?', (rating_key,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row)
            return None

    def _row_to_dict(self, row):
        """Convert a database row to a dictionary."""
        if not row:
            return None
        data = dict(row)
        for field in ['genres', 'directors', 'notification_data']:
            if data.get(field):
                try:
                    data[field] = json.loads(data[field])
                except json.JSONDecodeError:
                    data[field] = []
        return data

    def mark_as_rated(self, rating_key):
        """Mark a movie as rated."""
        with self._get_connection() as conn:
            conn.execute('UPDATE movies SET is_rated = 1 WHERE rating_key = ?', (rating_key,))

    def was_previously_watched(self, tmdb_id: str = None, title: str = None, year: int = None) -> bool:
        """Check if a movie was previously watched/rated (for rewatch detection).
        
        Uses TMDB ID as primary identifier, falls back to title+year.
        Returns True if the movie exists in DB with is_rated = 1.
        Backward compatible with old databases (gracefully handles missing data).
        """
        with self._get_connection() as conn:
            # First try to find by TMDB ID (most reliable)
            if tmdb_id:
                cursor = conn.execute('''
                    SELECT is_rated FROM movies 
                    WHERE tmdb_id = ? AND is_rated = 1
                    LIMIT 1
                ''', (tmdb_id,))
                if cursor.fetchone():
                    return True
            
            # Fallback: check by title + year
            if title and year:
                cursor = conn.execute('''
                    SELECT is_rated FROM movies 
                    WHERE title = ? AND year = ? AND is_rated = 1
                    LIMIT 1
                ''', (title, year))
                if cursor.fetchone():
                    return True
            
            return False

    def was_recently_notified(self, tmdb_id: str, title: str, year: int, last_viewed_at: datetime, threshold_seconds: int = 1800) -> bool:
        """Check if a notification was already sent for this movie (across all libraries).
        
        Uses TMDB ID as primary identifier, falls back to title+year.
        Returns True if a notification was sent within the threshold period.
        """
        with self._get_connection() as conn:
            # First try to find by TMDB ID (most reliable)
            if tmdb_id:
                cursor = conn.execute('''
                    SELECT last_viewed_at, notification_data FROM movies 
                    WHERE tmdb_id = ? AND notification_data IS NOT NULL AND notification_data != '{}'
                    ORDER BY last_viewed_at DESC LIMIT 1
                ''', (tmdb_id,))
                row = cursor.fetchone()
                if row and row['last_viewed_at']:
                    stored_viewed_at = datetime.fromisoformat(row['last_viewed_at'])
                    time_diff = abs((last_viewed_at - stored_viewed_at).total_seconds())
                    if time_diff < threshold_seconds:
                        return True
            
            # Fallback: check by title + year
            cursor = conn.execute('''
                SELECT last_viewed_at, notification_data FROM movies 
                WHERE title = ? AND year = ? AND notification_data IS NOT NULL AND notification_data != '{}'
                ORDER BY last_viewed_at DESC LIMIT 1
            ''', (title, year))
            row = cursor.fetchone()
            if row and row['last_viewed_at']:
                stored_viewed_at = datetime.fromisoformat(row['last_viewed_at'])
                time_diff = abs((last_viewed_at - stored_viewed_at).total_seconds())
                if time_diff < threshold_seconds:
                    return True
            
            return False

    def migrate_from_json(self, json_path):
        """Migrate data from the old JSON file to SQLite database."""
        if not os.path.exists(json_path):
            logger.warning(f"No JSON file found at {json_path} for migration")
            return

        try:
            logger.info(f"Starting migration from {json_path}")
            with open(json_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)

            essential_fields = {
                'rating_key', 'title', 'original_title', 'year', 'duration',
                'genres', 'directors', 'rating', 'thumb', 'last_viewed_at',
                'view_count', 'summary', 'tmdb_id', 'is_rated', 'notification_data'
            }

            migrated_count = 0
            with self._get_connection() as conn:
                for rating_key, movie_data in json_data.items():
                    try:
                        if not movie_data.get('title'):
                            continue

                        filtered_data = {}
                        
                        filtered_data['rating_key'] = rating_key
                        
                        for field in essential_fields:
                            if field in movie_data:
                                filtered_data[field] = movie_data[field]
                        
                        if 'genres' in filtered_data:
                            filtered_data['genres'] = json.dumps(filtered_data['genres'])
                        if 'directors' in filtered_data:
                            filtered_data['directors'] = json.dumps(filtered_data['directors'])
                        if 'notification' in movie_data:
                            filtered_data['notification_data'] = json.dumps(movie_data['notification'])

                        placeholders = ', '.join(['?'] * len(filtered_data))
                        columns = ', '.join(filtered_data.keys())
                        values = list(filtered_data.values())
                        
                        query = f'''
                            INSERT OR REPLACE INTO movies ({columns})
                            VALUES ({placeholders})
                        '''
                        
                        conn.execute(query, values)
                        migrated_count += 1

                        if migrated_count % 100 == 0:
                            logger.info(f"Migrated {migrated_count} movies...")
                            conn.commit()

                    except Exception as e:
                        logger.error(f"Error migrating movie {rating_key}: {str(e)}")
                        continue

                conn.commit()

            logger.info(f"Migration completed. {migrated_count} movies migrated successfully")
            
            # Backup the old JSON file
            backup_path = json_path + '.backup'
            os.rename(json_path, backup_path)
            logger.info(f"Original JSON file backed up to {backup_path}")

        except Exception as e:
            logger.error(f"Migration failed: {str(e)}")
            raise

class PlexMonitor:
    """Monitor Plex server for watched movies."""
    def __init__(self):
        self.plex = None
        self.db = MovieDatabase(MOVIE_DB_PATH)

        if os.path.exists(LEGACY_MOVIE_JSON_PATH):
            try:
                self.db.migrate_from_json(LEGACY_MOVIE_JSON_PATH)
            except Exception as e:
                logger.error(f"Failed to migrate JSON data: {str(e)}")

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

    def get_movie_details(self, movie) -> Optional[Dict]:
        """Extract relevant movie details from Plex."""
        logger.info(f"Extracting details for movie: {movie.title} ({movie.year})")
        try:
            hours, remaining_minutes = divmod(movie.duration // 60000, 60)
            duration_str = f"{hours}h {remaining_minutes}min" if hours else f"{remaining_minutes}min"
            
            tmdb_id = None
            for guid in getattr(movie, 'guids', []):
                if 'tmdb' in guid.id:
                    tmdb_id = guid.id.split('tmdb://')[-1]
                    break

            return {
                'title': movie.title,
                'original_title': getattr(movie, 'originalTitle', movie.title),
                'year': movie.year if hasattr(movie, 'year') else "Unknown",
                'duration': duration_str,
                'genres': ', '.join(genre.tag for genre in getattr(movie, 'genres', [])),
                'directors': ', '.join(director.tag for director in getattr(movie, 'directors', [])),
                'rating': getattr(movie, 'rating', "No Rating"),
                'thumb': movie.thumbUrl if hasattr(movie, 'thumbUrl') else None,
                'ratingKey': str(movie.ratingKey),
                'last_viewed_at': movie.lastViewedAt.isoformat() if movie.lastViewedAt else None,
                'view_count': getattr(movie, 'viewCount', 0),
                'summary': getattr(movie, 'summary', "No description available"),
                'tmdb_id': tmdb_id,
                'library': getattr(movie, 'librarySectionTitle', 'Unknown')
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
        self.app = build_application_container(RATING_DB_PATH)
        self.notify_channel = None
        self._runtime_loop = None
        self.rating_worker = RatingJobWorker(
            watch_event_repository=self.app.watch_events,
            rating_job_service=self.app.rating_job_service,
            rating_execution_service=self.app.rating_execution_service,
            success_callback=self._on_rating_job_success,
            failure_callback=self._on_rating_job_failure,
        )
    async def setup_hook(self):
        """Initialize bot and Plex connection."""
        check_latest_version()
        self._runtime_loop = asyncio.get_running_loop()
        
        if not await self.plex_monitor.initialize():
            logger.error("Bot startup aborted due to Plex connection failure")
            await self.close()

    def _dispatch_async(self, coroutine) -> None:
        if self._runtime_loop is None:
            return
        self._runtime_loop.call_soon_threadsafe(lambda: asyncio.create_task(coroutine))

    def _build_watch_event(self, movie_details: Dict) -> WatchEvent:
        watched_at = datetime.fromisoformat(movie_details["last_viewed_at"])
        event_key = f"{movie_details['ratingKey']}|{movie_details['last_viewed_at']}"
        return WatchEvent(
            id=str(uuid5(NAMESPACE_URL, event_key)),
            plex_rating_key=movie_details["ratingKey"],
            plex_guid_hash=movie_details.get("tmdb_id") or movie_details["ratingKey"],
            tmdb_id=movie_details.get("tmdb_id"),
            title=movie_details["title"],
            original_title=movie_details.get("original_title"),
            year=movie_details.get("year"),
            watched_at=watched_at,
            detected_at=self.app.clock.now(),
            view_count_at_watch=movie_details.get("view_count"),
            library_name=movie_details.get("library"),
            raw_payload=dict(movie_details),
        )

    def _ensure_watch_event(self, movie_details: Dict) -> WatchEvent:
        return self.app.watch_ingest.ingest(self._build_watch_event(movie_details))

    def _ensure_notification_record(self, watch_event_id: str, message_id: int) -> NotificationRecord:
        existing = self.app.notifications.get_by_discord_message_id(str(message_id))
        if existing is not None:
            return existing
        return self.app.notifications.add(
            NotificationRecord(
                id=self.app.id_factory.new("notification"),
                watch_event_id=watch_event_id,
                discord_channel_id=str(self.notify_channel.id),
                discord_message_id=str(message_id),
                discord_view_state="pending_rating",
                sent_at=self.app.clock.now(),
                updated_at=self.app.clock.now(),
            )
        )

    def _on_rating_job_success(self, job, result) -> None:
        self._dispatch_async(self._handle_rating_job_success(job, result))

    def _on_rating_job_failure(self, job, exc: Exception) -> None:
        self._dispatch_async(self._handle_rating_job_failure(job, exc))

    async def _handle_rating_job_success(self, job, result) -> None:
        event = self.app.watch_events.get_by_id(job.watch_event_id)
        if event is None:
            logger.error(f"Rating job {job.id} succeeded but its watch event is gone")
            return

        # Successes were silent: only failures logged, so a working rating left no trace
        # in plex_bot.log or the Discord log channel.
        logger.info(
            f"Logged {event.title} ({event.year}) on Letterboxd with "
            f"{result.rating_value} stars (entry {result.letterboxd_entry_id})"
        )

        notification = self.app.notifications.get_latest_for_watch_event(event.id)
        if notification is not None:
            self.app.notifications.update_view_state(notification.id, "succeeded", self.app.clock.now())
        if event.plex_rating_key:
            try:
                self.plex_monitor.db.mark_as_rated(event.plex_rating_key)
            except Exception as exc:
                logger.error(f"Failed to mark movie row as rated: {exc}")
        if not notification or not self.notify_channel:
            return
        try:
            message = await self.notify_channel.fetch_message(int(notification.discord_message_id))
            movie = self.plex_monitor.db.get_movie(event.plex_rating_key) or {}
            viewed_at_dt = event.watched_at
            view = MovieButtons(
                movie_title=movie.get("title", event.title),
                movie_year=movie.get("year", event.year),
                original_title=movie.get("original_title", event.original_title),
                last_viewed_at=viewed_at_dt.isoformat(),
                tmdb_id=movie.get("tmdb_id", event.tmdb_id),
                bot=self,
                rating_key=event.plex_rating_key,
                watch_event_id=event.id,
            )
            view.mark_succeeded(rating=result.rating_value)
            await message.edit(view=view)
        except Exception as exc:
            logger.error(f"Failed to update success state in Discord: {exc}")

    async def _handle_rating_job_failure(self, job, exc: Exception) -> None:
        event = self.app.watch_events.get_by_id(job.watch_event_id)
        if event is None:
            return
        notification = self.app.notifications.get_latest_for_watch_event(event.id)
        if notification is not None:
            self.app.notifications.update_view_state(notification.id, "failed", self.app.clock.now())
        if not notification or not self.notify_channel:
            logger.error(f"Rating job {job.id} failed: {exc}")
            return
        try:
            message = await self.notify_channel.fetch_message(int(notification.discord_message_id))
            movie = self.plex_monitor.db.get_movie(event.plex_rating_key) or {}
            view = MovieButtons(
                movie_title=movie.get("title", event.title),
                movie_year=movie.get("year", event.year),
                original_title=movie.get("original_title", event.original_title),
                last_viewed_at=event.watched_at.isoformat(),
                tmdb_id=movie.get("tmdb_id", event.tmdb_id),
                bot=self,
                rating_key=event.plex_rating_key,
                watch_event_id=event.id,
            )
            view.mark_failed()
            await message.edit(view=view)
        except Exception as notify_exc:
            logger.error(f"Failed to update failed state in Discord: {notify_exc}")
        logger.error(f"Rating job {job.id} failed: {exc}")

    async def restore_views(self):
        """Restore dropdown menus for the last 2 unwatched movies."""
        logger.info("Restoring dropdown menus for recent movies...")
        if not self.notify_channel:
            logger.error("Notify channel not initialized")
            return

        try:
            with self.plex_monitor.db._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT * FROM movies 
                    WHERE is_rated = 0 
                    AND notification_data IS NOT NULL 
                    AND notification_data != '{}'
                    ORDER BY last_viewed_at DESC 
                    LIMIT 4
                ''')
                movies = [dict(row) for row in cursor.fetchall()]

            restored_count = 0
            for movie in movies:
                try:
                    notification = json.loads(movie.get('notification_data', '{}'))
                    if not notification:
                        continue

                    message_id = notification.get('message_id')
                    if not message_id:
                        continue

                    message = await self.notify_channel.fetch_message(int(message_id))
                    watch_event = self._ensure_watch_event({
                        "ratingKey": movie["rating_key"],
                        "title": movie["title"],
                        "original_title": movie.get("original_title", movie["title"]),
                        "year": movie.get("year"),
                        "last_viewed_at": movie.get("last_viewed_at"),
                        "view_count": movie.get("view_count"),
                        "library": None,
                        "tmdb_id": movie.get("tmdb_id"),
                    })
                    self._ensure_notification_record(watch_event.id, int(message_id))
                    view = MovieButtons(
                        movie_title=movie['title'],
                        movie_year=movie['year'],
                        original_title=movie.get('original_title', movie['title']),
                        last_viewed_at=movie.get('last_viewed_at'),
                        tmdb_id=movie.get('tmdb_id'),
                        bot=self,
                        rating_key=movie['rating_key'],
                        watch_event_id=watch_event.id,
                    )
                    await message.edit(view=view)
                    restored_count += 1
                    logger.info(f"Restored dropdown for: {movie['title']} ({movie['year']})")
                    await asyncio.sleep(0.5)

                except discord.NotFound:
                    with self.plex_monitor.db._get_connection() as conn:
                        conn.execute('''
                            UPDATE movies 
                            SET notification_data = '{}'
                            WHERE rating_key = ?
                        ''', (movie['rating_key'],))
                    logger.info(f"Removed deleted message reference for {movie['title']}")
                except Exception as e:
                    logger.error(f"Failed to restore dropdown for {movie['title']}: {str(e)}")

            logger.info(f"Restored {restored_count} dropdowns")

        except Exception as e:
            logger.error(f"Error in restore_views: {str(e)}")

    @tasks.loop(minutes=15)
    async def check_recently_watched(self):
        """Check for recently watched movies and notify."""
        if not self.notify_channel:
            logger.error("Notify channel not initialized!")
            return

        try:
            if not self.plex_monitor.plex:
                logger.warning("Plex connection lost, attempting to reconnect...")
                if not await self.plex_monitor.initialize():
                    logger.error("Failed to reconnect to Plex server")
                    return

            # Use history() to get actually played movies with correct library info
            # This shows which library the movie was actually played from
            history = self.plex_monitor.plex.history(maxresults=50)
            current_time = datetime.now()
            
            # Get the configured user's account ID once
            user_account_id = None
            try:
                for account in self.plex_monitor.plex.systemAccounts():
                    # Check both name (username/email) and title (display name)
                    if account.name == PLEX_USERNAME or getattr(account, 'title', '') == PLEX_USERNAME:
                        user_account_id = account.id
                        break
            except Exception as e:
                logger.warning(f"Could not get user account ID: {str(e)}")

            for history_item in history:
                try:
                    # Only process movies
                    if history_item.type != 'movie':
                        continue
                    
                    # Only process movies watched by the configured user
                    if user_account_id is not None and history_item.accountID != user_account_id:
                        continue
                    
                    # Check if viewed recently (within 30 minutes)
                    last_viewed = getattr(history_item, 'viewedAt', None)
                    if not last_viewed or (current_time - last_viewed).total_seconds() > 1800:
                        continue

                    # Get the full movie object to extract all details
                    try:
                        movie = self.plex_monitor.plex.fetchItem(history_item.ratingKey)
                    except Exception as e:
                        logger.warning(f"Could not fetch movie details for {history_item.title}: {str(e)}")
                        continue

                    if self.plex_monitor.is_movie_currently_playing(movie.title):
                        logger.info(f"Movie {movie.title} ({movie.year}) is currently playing, skipping notification")
                        continue

                    movie_details = self.plex_monitor.get_movie_details(movie)
                    if not movie_details:
                        continue
                    
                    # Override last_viewed_at with the actual viewing time from history
                    movie_details['last_viewed_at'] = last_viewed.isoformat() if last_viewed else None

                    # Check if library is excluded
                    if movie_details.get('library') in EXCLUDED_LIBRARIES:
                        logger.info(f"Movie {movie_details['title']} ({movie_details['year']}) is from excluded library '{movie_details['library']}', skipping")
                        continue

                    movie_key = movie_details['ratingKey']
                    
                    # Check if this movie was already notified (handles duplicates across libraries)
                    if self.plex_monitor.db.was_recently_notified(
                        tmdb_id=movie_details.get('tmdb_id'),
                        title=movie_details['title'],
                        year=movie_details['year'],
                        last_viewed_at=last_viewed,
                        threshold_seconds=1800
                    ):
                        logger.info(f"Movie {movie_details['title']} ({movie_details['year']}) was already notified (duplicate across libraries), skipping")
                        continue
                    
                    with self.plex_monitor.db._get_connection() as conn:
                        cursor = conn.execute('SELECT * FROM movies WHERE rating_key = ?', (movie_key,))
                        stored_movie = cursor.fetchone()
                        
                    stored_view_count = stored_movie['view_count'] if stored_movie else 0
                    current_view_count = movie_details['view_count']
                    stored_last_viewed = datetime.fromisoformat(stored_movie['last_viewed_at']) if stored_movie and stored_movie['last_viewed_at'] else None

                    if (not stored_movie or 
                        current_view_count > stored_view_count or 
                        (stored_last_viewed and (last_viewed - stored_last_viewed).total_seconds() > 7200)):

                        try:
                            # Add previous viewing date for rewatch display
                            if stored_last_viewed:
                                movie_details['previous_viewed_at'] = stored_last_viewed.isoformat()
                            watch_event = self._ensure_watch_event(movie_details)
                            
                            embed, file = await self.create_movie_embed(movie_details)
                            view = MovieButtons(
                                movie_title=movie_details['title'],
                                movie_year=movie_details['year'],
                                original_title=movie_details.get('original_title', movie_details['title']),
                                last_viewed_at=movie_details.get('last_viewed_at'),
                                tmdb_id=movie_details.get('tmdb_id'),
                                bot=self,
                                rating_key=movie_details['ratingKey'],
                                watch_event_id=watch_event.id,
                            )

                            mention = f"<@{DISCORD_USER_ID}>" if DISCORD_USER_ID else ""

                            message = await self.notify_channel.send(
                                content=mention,
                                embed=embed,
                                file=file,
                                view=view
                            )

                            movie_details['notification_data'] = json.dumps({
                                'message_id': str(message.id),
                                'channel_id': str(self.notify_channel.id)
                            })
                            movie_details['is_rated'] = False

                            self.plex_monitor.db.save_movie(movie_details)
                            self._ensure_notification_record(watch_event.id, message.id)
                            logger.info(f"Notification sent for: {movie_details['title']} ({movie_details['year']})")
                            
                            await asyncio.sleep(1)
                            
                        except Exception as e:
                            logger.error(f"Error sending notification for {movie.title}: {str(e)}")
                            continue
                            
                except Exception as e:
                    logger.error(f"Error processing movie {getattr(movie, 'title', 'Unknown')}: {str(e)}")
                    continue

        except requests.RequestException as e:
            # The loop schedules its next run itself. Restarting it here created a tight
            # reconnect loop and flooded Discord whenever Plex was briefly offline.
            self.plex_monitor.plex = None
            logger.warning(f"Plex unavailable during recently-watched check; retrying next interval: {e}")
        except Exception as e:
            logger.error(f"Error in check_recently_watched task: {str(e)}")

    async def create_movie_embed(self, movie_details: Dict):
        """Create Discord embed for movie notification (placeholder, moved to utils)."""
        from utils import create_movie_embed
        return await create_movie_embed(movie_details)

    @tasks.loop(seconds=15)
    async def process_rating_jobs(self):
        """Continuously drain queued rating jobs using the worker path."""
        worker_id = f"discord-bot-{self.user.id if self.user else 'boot'}"
        while True:
            try:
                processed = await asyncio.to_thread(self.rating_worker.run_once, worker_id)
            except Exception as exc:
                logger.error(f"Rating worker loop failed for one job: {exc}")
                break
            if not processed:
                break

    async def on_ready(self):
        """Handle bot startup and channel setup."""
        try:
            logger.info(f"Bot started as: {self.user}")
            
            # Set presence as Custom Status
            await self.change_presence(activity=discord.CustomActivity(name="🎬 Watching Plex"))
            
            max_retries = 3
            retry_delay = 5
            for attempt in range(max_retries):
                try:
                    self.notify_channel = self.get_channel(NOTIFY_CHANNEL_ID)
                    if self.notify_channel:
                        break
                    logger.warning(f"Attempt {attempt + 1}/{max_retries}: Channel {NOTIFY_CHANNEL_ID} not found!")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                except Exception as e:
                    logger.error(f"Error getting channel: {str(e)}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
            
            if not self.notify_channel:
                logger.error(f"Failed to find channel {NOTIFY_CHANNEL_ID} after {max_retries} attempts!")
                await self.close()
                return
                
            logger.info(f"Notification channel found: #{self.notify_channel.name}")
            
            try:
                await self.restore_views()
            except Exception as e:
                logger.error(f"Error restoring views: {str(e)}")
            
            if not self.check_recently_watched.is_running():
                try:
                    self.check_recently_watched.start()
                except Exception as e:
                    logger.error(f"Failed to start check_recently_watched task: {str(e)}")
            else:
                logger.info("check_recently_watched task is already running")

            if not self.process_rating_jobs.is_running():
                try:
                    self.process_rating_jobs.start()
                except Exception as e:
                    logger.error(f"Failed to start process_rating_jobs task: {str(e)}")
            else:
                logger.info("process_rating_jobs task is already running")
                
        except Exception as e:
            logger.error(f"Error in on_ready: {str(e)}")
            await self.close()

def main():
    """Run the Plex Discord bot."""
    # discord.py only unwinds cleanly on KeyboardInterrupt, and `docker stop` sends
    # SIGTERM. Without this the process ignored it and Docker escalated to SIGKILL after
    # its timeout, so every restart was a hard kill (exit 137).
    def _terminate(signum, _frame):
        logger.info(f"Received signal {signum}; shutting down")
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)

    bot = PlexDiscordBot()
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
