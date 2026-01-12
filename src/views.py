# src/views.py
import discord
from discord.ui import Button, View, Modal, TextInput, Select, Label
from discord import TextStyle, SelectOption
import logging
from datetime import datetime
import requests
from letterboxd_integration import login, get_film_id_selenium, save_diary_entry

logger = logging.getLogger('PlexBot')


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
                 rating_key: str = None, is_rewatch: bool = False,
                 parent_view=None, original_message=None):
        super().__init__()
        
        self.movie_title = movie_title
        self.movie_year = movie_year
        self.original_title = original_title or movie_title
        self.last_viewed_at = last_viewed_at
        self.tmdb_id = tmdb_id
        self.bot = bot
        self.rating_key = rating_key
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
        """Handle modal submission and log to Letterboxd."""
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
            
            # Log to Letterboxd
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                "Referer": "https://letterboxd.com/",
            })
            csrf_token = login(session)
            film_id = get_film_id_selenium(
                session, self.movie_title, self.movie_year, 
                self.original_title, tmdb_id=self.tmdb_id
            )
            if not film_id:
                raise ValueError(f"Could not find film ID for '{self.original_title}' ({self.movie_year})")
            
            save_diary_entry(
                session, csrf_token, film_id, rating,
                viewing_date=self.last_viewed_at,
                rewatch=is_rewatch,
                liked=is_liked,
                tags=tags_text,
                review=review_text
            )
            
            # Build success message (matching original format)
            viewed_at_dt = datetime.fromisoformat(self.last_viewed_at) if self.last_viewed_at else datetime.now()
            
            embed = discord.Embed(
                title="Rating Successful!",
                description=f"**{self.movie_title} ({self.movie_year})** rated **{rating} ★** on Letterboxd.",
                color=discord.Color.green(),
                timestamp=viewed_at_dt
            )
            embed.set_author(name="Letterboxd Rating", icon_url="https://i.imgur.com/0Yd2L4i.png")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # Disable the button after successful submission (matching original format)
            if self.parent_view and self.original_message:
                try:
                    self.parent_view.diary_button.disabled = True
                    self.parent_view.diary_button.label = f"Rated {rating} ★ for {viewed_at_dt.strftime('%d.%m.%Y %H:%M')}"
                    self.parent_view.diary_button.style = discord.ButtonStyle.secondary
                    await self.original_message.edit(view=self.parent_view)
                except Exception as e:
                    logger.warning(f"Could not disable button: {str(e)}")
            
            # Mark as rated in database
            if self.bot and self.bot.plex_monitor and self.rating_key:
                try:
                    with self.bot.plex_monitor.db._get_connection() as conn:
                        conn.execute('UPDATE movies SET is_rated = 1 WHERE rating_key = ?', (self.rating_key,))
                        conn.commit()
                        logger.info(f"Marked {self.movie_title} ({self.movie_year}) as rated in database")
                except Exception as e:
                    logger.error(f"Failed to update rating status in database: {str(e)}")
            
        except Exception as e:
            logger.error(f"Failed to log movie on Letterboxd: {str(e)}")
            embed = discord.Embed(
                title="❌ Diary Entry Failed!",
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
                 last_viewed_at: str = None, tmdb_id: str = None, bot=None, rating_key: str = None):
        super().__init__(timeout=None)
        self.movie_title = movie_title
        self.movie_year = movie_year
        self.original_title = original_title or movie_title
        self.last_viewed_at = last_viewed_at
        self.tmdb_id = tmdb_id
        self.bot = bot
        self.rating_key = rating_key
        
        # Create the diary entry button
        self.diary_button = Button(
            label="📝 Diary Entry",
            style=discord.ButtonStyle.primary,
            custom_id=f"diary_entry_{movie_title}_{movie_year}_{last_viewed_at or 'latest'}"
        )
        self.diary_button.callback = self.diary_button_callback
        self.add_item(self.diary_button)
    
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
            is_rewatch=is_rewatch,
            parent_view=self,
            original_message=interaction.message
        )
        await interaction.response.send_modal(modal)