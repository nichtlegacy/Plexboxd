# src/views.py
import discord
from discord.ui import Button, View, Modal, TextInput, Select, Label
from discord import TextStyle, SelectOption
import logging

from plexboxd.application.rating_jobs import RatingJobAlreadyCompletedError
from plexboxd.domain.models import RatingRequest
from discord_time import to_aware_datetime

logger = logging.getLogger('PlexBot')

# Duplicated from plex_bot rather than imported: plex_bot imports this module, so taking
# the constant from there would be a circular import.
LETTERBOXD_LOGO = "https://i.imgur.com/0Yd2L4i.png"


def format_stars(rating: float) -> str:
    """Render a rating the way Letterboxd writes it: ★★★½ rather than "3.5 stars".

    Button labels cap at 80 characters and are plain text, so this stays compact and
    needs no markdown.
    """
    try:
        value = float(rating)
    except (TypeError, ValueError):
        return str(rating)
    full = int(value)
    half = "½" if value - full >= 0.5 else ""
    # Half a star on its own would render as a lone "½", so fall back to the number.
    if not full and half:
        return "½ star"
    return f"{'★' * full}{half}"


def build_queue_confirmation(
    *,
    movie_title: str,
    movie_year: int,
    rating: float,
    liked: bool,
    rewatch: bool,
    tags: tuple[str, ...] = (),
    review: str = "",
) -> discord.Embed:
    """Confirm what the user just chose, before the write has happened.

    Deliberately reflects the choices back rather than reporting queue mechanics: the
    job id it used to lead with is only useful for `plexboxd-cli inspect-job`, so it goes
    to the log instead. Flags appear only when set, so an unadorned rating stays a single
    short line.
    """
    details = [format_stars(rating)]
    if liked:
        details.append("❤️ Liked")
    if rewatch:
        details.append("🔄 Rewatch")

    embed = discord.Embed(
        title=f"{movie_title} ({movie_year})",
        description="\n".join(
            [
                "  ".join(details),
                "",
                "-# Writing to your Letterboxd diary — the button updates when it lands.",
            ]
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    if tags:
        embed.add_field(name="🏷️ Tags", value=" ".join(f"`{tag}`" for tag in tags), inline=False)
    if review and review.strip():
        text = review.strip()
        preview = text if len(text) <= 300 else f"{text[:300]}…"
        # Block quote so a multi-line review cannot be mistaken for bot copy.
        embed.add_field(name="📝 Review", value=f">>> {preview}", inline=False)
    embed.set_author(name="Letterboxd", icon_url=LETTERBOXD_LOGO)
    return embed


def _parse_tags(tags_text: str) -> tuple[str, ...]:
    """Split free-form tag input into individual Letterboxd tags.

    Accepts comma- or whitespace-separated input and drops duplicates while keeping
    the order the user typed.
    """
    if not tags_text:
        return ()
    parts = [part.strip() for part in tags_text.replace(',', ' ').split()]
    seen: dict[str, None] = {}
    for part in parts:
        if part:
            seen.setdefault(part, None)
    return tuple(seen)


class DiaryEntryModal(Modal, title='Letterboxd Diary Entry'):
    """Modal for creating a Letterboxd diary entry with extended options."""
    
    # Rating dropdown (required) - 0.5 to 5.0 stars with real star emojis
    rating = Label(
        text='⭐ Rating',
        description='Select your rating for this film.',
        component=Select(
            placeholder='Choose a rating...',
            options=[
                SelectOption(label=f"{'⭐' * int(r)} {'½' if r % 1 else ''}", value=str(r), description=f'{r} stars')
                for r in [round(i * 0.5, 1) for i in range(1, 11)]
            ],
        ),
    )
    
    # Rewatch dropdown (optional) - First Watch is default
    rewatch = Label(
        text='🔄 Rewatch?',
        description='Is this a rewatch?',
        component=Select(
            placeholder='First watch or rewatch?',
            options=[
                SelectOption(label='🎬 First Watch', value='no', description='This is my first time watching', default=True),
                SelectOption(label='🔄 Rewatch', value='yes', description='I have seen this before'),
            ],
        ),
    )
    
    # Liked dropdown (optional) - No is default
    liked = Label(
        text='❤️ Liked?',
        description='Did you love this film?',
        component=Select(
            placeholder='Did you like it?',
            options=[
                SelectOption(label='No', value='no', default=True),
                SelectOption(label='Liked ❤️', value='yes', description='Add to your liked films'),
            ],
        ),
    )
    
    # Tags (optional)
    tags = Label(
        text='🏷️ Tags',
        description='Add tags to categorize your viewing (optional).',
        component=TextInput(
            style=TextStyle.short,
            placeholder='horror, cinema, rewatched with friends',
            max_length=200,
            required=False,
        ),
    )
    
    # Review (optional)
    review = Label(
        text='📝 Review',
        description='Write your thoughts about the film (optional).',
        component=TextInput(
            style=TextStyle.paragraph,
            placeholder='What did you think of the film?',
            max_length=1000,
            required=False,
        ),
    )
    
    def __init__(self, movie_title: str, movie_year: int, original_title: str = None,
                 last_viewed_at: str = None, tmdb_id: str = None, bot=None,
                 rating_key: str = None, watch_event_id: str = None, is_rewatch: bool = False,
                 parent_view=None, original_message=None):
        super().__init__()
        
        self.movie_title = movie_title
        self.movie_year = movie_year
        self.original_title = original_title or movie_title
        self.last_viewed_at = last_viewed_at
        self.tmdb_id = tmdb_id
        self.bot = bot
        self.rating_key = rating_key
        self.watch_event_id = watch_event_id
        self.parent_view = parent_view
        self.original_message = original_message
        
        # Update modal title with movie name
        self.title = f'Log: {movie_title[:35]}{"..." if len(movie_title) > 35 else ""}'
        
        # Pre-select rewatch if detected
        if is_rewatch:
            assert isinstance(self.rewatch.component, Select)
            self.rewatch.component.options = [
                SelectOption(label='🎬 First Watch', value='no', description='This is my first time watching'),
                SelectOption(label='🔄 Rewatch', value='yes', description='I have seen this before', default=True),
            ]
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission by enqueueing a Letterboxd job."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Extract values from components
            assert isinstance(self.rating.component, Select)
            assert isinstance(self.rewatch.component, Select)
            assert isinstance(self.liked.component, Select)
            assert isinstance(self.tags.component, TextInput)
            assert isinstance(self.review.component, TextInput)

            rating = float(self.rating.component.values[0])
            is_rewatch = self.rewatch.component.values[0] == 'yes'
            is_liked = self.liked.component.values[0] == 'yes'
            tags_text = self.tags.component.value.strip() if self.tags.component.value else ""
            review_text = self.review.component.value.strip() if self.review.component.value else ""
            if not self.bot or not getattr(self.bot, "app", None) or not self.watch_event_id:
                raise RuntimeError("Rating queue is not initialized for this notification")

            notification = None
            if interaction.message is not None:
                notification = self.bot.app.notifications.get_by_discord_message_id(str(interaction.message.id))

            request = RatingRequest(
                rating=rating,
                liked=is_liked,
                rewatch=is_rewatch,
                requested_by_discord_user_id=str(interaction.user.id),
                tags=_parse_tags(tags_text),
                review=review_text,
            )

            job = self.bot.app.rating_job_service.enqueue(
                self.watch_event_id,
                notification.id if notification else None,
                request,
            )
            if notification is not None:
                self.bot.app.notifications.update_view_state(
                    notification.id,
                    "queued",
                    self.bot.app.clock.now(),
                )

            # The job id belongs in the log, not on screen: it is only useful for
            # `plexboxd-cli inspect-job`, and it dominated a message whose job is to
            # confirm what the user just chose.
            logger.info(
                f"Queued {self.movie_title} ({self.movie_year}) with {rating} stars "
                f"(job {job.id})"
            )

            embed = build_queue_confirmation(
                movie_title=self.movie_title,
                movie_year=self.movie_year,
                rating=rating,
                liked=is_liked,
                rewatch=is_rewatch,
                tags=_parse_tags(tags_text),
                review=review_text,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            if self.parent_view and self.original_message:
                try:
                    self.parent_view.mark_queued()
                    await self.original_message.edit(view=self.parent_view)
                except Exception as e:
                    logger.warning(f"Could not update queued button state: {str(e)}")
        except RatingJobAlreadyCompletedError:
            viewed_at_dt = to_aware_datetime(self.last_viewed_at) or discord.utils.utcnow()
            embed = discord.Embed(
                title="Already in your diary",
                description=(
                    f"**{self.movie_title} ({self.movie_year})** was already logged on Letterboxd, "
                    "so nothing was sent again."
                ),
                color=discord.Color.green(),
                timestamp=viewed_at_dt,
            )
            embed.set_author(name="Letterboxd", icon_url=LETTERBOXD_LOGO)
            await interaction.followup.send(embed=embed, ephemeral=True)
            if self.parent_view and self.original_message:
                self.parent_view.mark_succeeded(rating=rating)
                await self.original_message.edit(view=self.parent_view)
        except Exception as e:
            logger.error(f"Failed to enqueue movie for Letterboxd: {str(e)}")
            reason = str(e).strip() or e.__class__.__name__
            embed = discord.Embed(
                title="Could not queue this entry",
                description=(
                    f"**{self.movie_title} ({self.movie_year})** was not sent to Letterboxd.\n"
                    f"-# {reason[:300]}{'…' if len(reason) > 300 else ''}"
                ),
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(name="Letterboxd", icon_url=LETTERBOXD_LOGO)
            await interaction.followup.send(embed=embed, ephemeral=True)
    
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Modal error: {error}")
        await interaction.response.send_message('An error occurred while processing your diary entry.', ephemeral=True)


class MovieButtons(View):
    """Interactive button for logging movies on Letterboxd."""
    
    def __init__(self, movie_title: str, movie_year: int, original_title: str = None,
                 last_viewed_at: str = None, tmdb_id: str = None, bot=None,
                 rating_key: str = None, watch_event_id: str = None):
        super().__init__(timeout=None)
        self.movie_title = movie_title
        self.movie_year = movie_year
        self.original_title = original_title or movie_title
        self.last_viewed_at = last_viewed_at
        self.tmdb_id = tmdb_id
        self.bot = bot
        self.rating_key = rating_key
        self.watch_event_id = watch_event_id
        
        # Create the diary entry button
        self.diary_button = Button(
            label="📝 Diary Entry",
            style=discord.ButtonStyle.primary,
            custom_id=f"diary_entry_{watch_event_id or rating_key or movie_year}"
        )
        self.diary_button.callback = self.diary_button_callback
        self.add_item(self.diary_button)

    def mark_queued(self) -> None:
        self.diary_button.disabled = True
        self.diary_button.label = "Sending to Letterboxd…"
        self.diary_button.emoji = "⏳"
        self.diary_button.style = discord.ButtonStyle.secondary

    def mark_succeeded(self, rating: float) -> None:
        """Show the rating that was written.

        No date here: the embed's own timestamp already says when the film was watched,
        and button labels are plain text, so a date could not be localised per viewer and
        was rendered in the server's timezone for everyone.
        """
        self.diary_button.disabled = True
        self.diary_button.label = f"Logged {format_stars(rating)}"
        self.diary_button.emoji = "✅"
        self.diary_button.style = discord.ButtonStyle.success

    def mark_failed(self) -> None:
        self.diary_button.disabled = False
        self.diary_button.label = "Retry"
        self.diary_button.emoji = "🔁"
        self.diary_button.style = discord.ButtonStyle.danger
    
    async def diary_button_callback(self, interaction: discord.Interaction):
        """Open the diary entry modal when button is clicked."""
        # Check if this is a rewatch
        is_rewatch = False
        if self.bot and self.bot.plex_monitor:
            try:
                is_rewatch = self.bot.plex_monitor.db.was_previously_watched(
                    tmdb_id=self.tmdb_id,
                    title=self.movie_title,
                    year=self.movie_year
                )
                if is_rewatch:
                    logger.info(f"Detected rewatch for {self.movie_title} ({self.movie_year})")
            except Exception as e:
                logger.warning(f"Could not check rewatch status: {str(e)}")
        
        modal = DiaryEntryModal(
            movie_title=self.movie_title,
            movie_year=self.movie_year,
            original_title=self.original_title,
            last_viewed_at=self.last_viewed_at,
            tmdb_id=self.tmdb_id,
            bot=self.bot,
            rating_key=self.rating_key,
            watch_event_id=self.watch_event_id,
            is_rewatch=is_rewatch,
            parent_view=self,
            original_message=interaction.message
        )
        await interaction.response.send_modal(modal)
