# src/views.py
import discord
from discord.ui import Button, View, Modal, TextInput, Select, Label
from discord import TextStyle, SelectOption
import logging
from datetime import datetime

from plexboxd.application.rating_jobs import RatingJobAlreadyCompletedError
from plexboxd.domain.models import RatingRequest

logger = logging.getLogger('PlexBot')


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

            embed = discord.Embed(
                title="Queued For Letterboxd",
                description=(
                    f"**{self.movie_title} ({self.movie_year})** was queued with **{rating} ★**.\n"
                    f"Job: `{job.id}`"
                ),
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            parsed_tags = _parse_tags(tags_text)
            if parsed_tags:
                embed.add_field(name="Tags", value=", ".join(parsed_tags), inline=False)
            if review_text:
                preview = review_text if len(review_text) <= 200 else f"{review_text[:200]}…"
                embed.add_field(name="Review", value=preview, inline=False)
            embed.set_author(name="Letterboxd Queue", icon_url="https://i.imgur.com/0Yd2L4i.png")
            await interaction.followup.send(embed=embed, ephemeral=True)

            if self.parent_view and self.original_message:
                try:
                    self.parent_view.mark_queued()
                    await self.original_message.edit(view=self.parent_view)
                except Exception as e:
                    logger.warning(f"Could not update queued button state: {str(e)}")
        except RatingJobAlreadyCompletedError:
            viewed_at_dt = datetime.fromisoformat(self.last_viewed_at) if self.last_viewed_at else datetime.now()
            embed = discord.Embed(
                title="Already Rated",
                description=f"**{self.movie_title} ({self.movie_year})** already has a successful Letterboxd result.",
                color=discord.Color.green(),
                timestamp=viewed_at_dt,
            )
            embed.set_author(name="Letterboxd Rating", icon_url="https://i.imgur.com/0Yd2L4i.png")
            await interaction.followup.send(embed=embed, ephemeral=True)
            if self.parent_view and self.original_message:
                self.parent_view.mark_succeeded(rating=rating, viewed_at_dt=viewed_at_dt)
                await self.original_message.edit(view=self.parent_view)
        except Exception as e:
            logger.error(f"Failed to enqueue movie for Letterboxd: {str(e)}")
            embed = discord.Embed(
                title="❌ Queue Failed!",
                description=f"Error: {str(e)[:300]}{'...' if len(str(e)) > 300 else ''}",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.set_author(name="Letterboxd Error", icon_url="https://i.imgur.com/0Yd2L4i.png")
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
        self.diary_button.label = "Queued for Letterboxd..."
        self.diary_button.style = discord.ButtonStyle.secondary

    def mark_succeeded(self, rating: float, viewed_at_dt: datetime) -> None:
        self.diary_button.disabled = True
        self.diary_button.label = f"Rated {rating} ★ for {viewed_at_dt.strftime('%d.%m.%Y %H:%M')}"
        self.diary_button.style = discord.ButtonStyle.secondary

    def mark_failed(self) -> None:
        self.diary_button.disabled = False
        self.diary_button.label = "Retry Diary Entry"
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
