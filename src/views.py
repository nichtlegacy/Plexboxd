import discord
from discord.ui import Select, View
import logging
from datetime import datetime
import requests
from letterboxd_integration import login, get_film_id_selenium, save_diary_entry

logger = logging.getLogger('PlexBot')

class MovieButtons(View):
    """Interactive buttons for rating movies on Letterboxd."""
    def __init__(self, movie_title: str, movie_year: int, original_title: str = None, last_viewed_at: str = None, tmdb_id: str = None, bot=None):
        super().__init__(timeout=None)
        self.movie_title = movie_title
        self.movie_year = movie_year
        self.original_title = original_title or movie_title
        self.last_viewed_at = last_viewed_at
        self.tmdb_id = tmdb_id
        self.bot = bot  # Store bot instance to access plex_monitor

        rating_options = [
            discord.SelectOption(label=f"{rating} ★", value=str(rating))
            for rating in [round(i * 0.5, 1) for i in range(1, 11)]
        ]
        self.rating_select = Select(
            custom_id=f"rate_movie_{movie_title}_{movie_year}_{last_viewed_at or 'latest'}",
            placeholder="Rate this movie",
            min_values=1,
            max_values=1,
            options=rating_options
        )
        self.rating_select.callback = self.rating_callback
        self.add_item(self.rating_select)

    async def rating_callback(self, interaction: discord.Interaction):
        """Handle rating selection and update Letterboxd."""
        rating = float(interaction.data['values'][0])
        await interaction.response.defer(ephemeral=True)

        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                "Referer": "https://letterboxd.com/",
            })
            csrf_token = login(session)
            film_id = get_film_id_selenium(session, self.movie_title, self.movie_year, self.original_title, tmdb_id=self.tmdb_id)
            if not film_id:
                raise ValueError(f"Could not find film ID for '{self.original_title}' ({self.movie_year})")
            save_diary_entry(session, csrf_token, film_id, rating, viewing_date=self.last_viewed_at)

            viewed_at_display = self.last_viewed_at
            viewed_at_dt = datetime.fromisoformat(viewed_at_display) if viewed_at_display else datetime.now()

            self.rating_select.disabled = True
            self.rating_select.placeholder = f"Rated {rating} ★ for {viewed_at_dt.strftime('%d.%m.%Y %H:%M')}"
            embed = discord.Embed(
                title="Rating Successful!",
                description=f"**{self.movie_title} ({self.movie_year})** rated **{rating} ★** on Letterboxd.",
                color=discord.Color.green(),
                timestamp=viewed_at_dt
            )

            embed.set_author(name="Letterboxd Rating", icon_url="https://i.imgur.com/0Yd2L4i.png")
            await interaction.followup.send(embed=embed, ephemeral=True)
            await interaction.message.edit(view=self)

            # Update movie data to mark as rated
            if self.bot and self.bot.plex_monitor:
                for movie_key, movie_data in self.bot.plex_monitor.watched_movies.items():
                    if (movie_data['title'] == self.movie_title and 
                        movie_data['year'] == self.movie_year and 
                        movie_data.get('last_viewed_at') == self.last_viewed_at):
                        movie_data['is_rated'] = True
                        self.bot.plex_monitor.save_movie_data()
                        logger.info(f"Marked {self.movie_title} ({self.movie_year}) as rated in movie_data.json")
                        break

        except Exception as e:
            logger.error(f"Failed to rate movie on Letterboxd: {str(e)}")
            embed = discord.Embed(
                title="Rating Failed!",
                description=f"Error: {str(e)[:200]}{'...' if len(str(e)) > 200 else ''}",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.set_author(name="Letterboxd Error", icon_url="https://i.imgur.com/0Yd2L4i.png")
            await interaction.followup.send(embed=embed, ephemeral=True)