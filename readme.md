# Plexboxd

[![GitHub release](https://img.shields.io/github/release/nichtlegacy/plexboxd.svg?style=flat-square)](https://github.com/nichtlegacy/plexboxd/releases/latest)
![Made with Python](https://img.shields.io/badge/Made%20with-Python-3776AB?style=flat-square&logo=python&logoColor=white)
![Discord](https://img.shields.io/badge/Discord-Bot-5865F2?style=flat-square&logo=discord&logoColor=white)
![Plex](https://img.shields.io/badge/Plex-Server-E5A00D?style=flat-square&logo=plex&logoColor=white)
![Letterboxd](https://img.shields.io/badge/Letterboxd-Integration-00D735?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)


Plexboxd is a Discord bot that automatically tracks movies watched on Plex and integrates with Letterboxd to log and rate them. It monitors your Plex server for watched content and sends an embed with all necessary information to a Discord channel, allowing you to rate films directly from Discord - which then automatically adds them to your Letterboxd diary.

![Plexboxd Notification Example](https://i.imgur.com/Sm9RIqc.png)

## Features

- 🎬 **Plex Integration**: Monitors your Plex server for newly watched movies
- 🤖 **Discord Notifications**: Sends an embed with all necessary movie information to a Discord channel when you finish watching a film
- ⭐ **Letterboxd Integration**: Rate movies directly from Discord with seamless Letterboxd logging
- 📊 **Watch History**: Keeps track of your viewing history with timestamps and rating data
- 🌙 **Date Threshold**: Configurable time threshold to determine if late-night watches count for the current or previous day

## Requirements

- Python 3.7+
- A Discord account and a Discord server where you have admin permissions
- A Plex Media Server
- A Letterboxd account

## Installation

1. Clone the repository
```bash
git clone https://github.com/nichtlegacy/plexboxd.git
cd plexboxd
```

2. Install the required dependencies
```bash
pip install -r requirements.txt
```

3. Create a `.env` file based on the provided `.env.example`
```bash
cp .env.example .env
```

4. Fill in the required environment variables in the `.env` file (see Configuration section)

5. Start the bot
```bash
python src/plex_bot.py
```

## Configuration

### Environment Variables

The `.env` file contains all the necessary configuration parameters:

#### Discord Configuration
- `DISCORD_TOKEN`: Your Discord bot token from the [Discord Developer Portal](https://discord.com/developers/applications)
- `DISCORD_LOGGING_WEBHOOK_URL`: Webhook URL for sending logs to a Discord channel
- `NOTIFY_CHANNEL_ID`: ID of the Discord channel where movie notifications will be sent
- `GUILD_ID`: ID of your Discord server
- `DISCORD_USER_ID`: Your Discord user ID to be notified when a movie is watched

#### Plex Configuration
- `PLEX_USERNAME`: Your Plex account username
- `PLEX_TOKEN`: Your Plex authentication token
- `PLEX_SERVER_URL`: URL of your Plex server (e.g., `http://192.168.1.100:32400`)
- `PLEX_LIBRARY_NAME`: Name of your Plex movie library (e.g., `Movies`)

#### Letterboxd Configuration
- `LETTERBOXD_USERNAME`: Your Letterboxd username
- `LETTERBOXD_PASSWORD`: Your Letterboxd password
- `DATE_THRESHOLD_HOUR`: Hour (in 24-hour format) to determine the cutoff for assigning movie watch dates (default: 7)

### How to Get Required Tokens

#### Discord Bot Token
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to the "Bot" tab and create a bot
4. Copy the token and add it to your `.env` file

#### Discord Channel and Guild IDs
1. Enable Developer Mode in Discord (User Settings > Advanced > Developer Mode)
2. Right-click on your server icon and select "Copy ID" for the Guild ID
3. Right-click on the notification channel and select "Copy ID" for the Channel ID

#### Plex Token
1. Log in to your Plex Web App
2. Play any media item
3. Right-click anywhere and choose "View Page Source"
4. Search for "X-Plex-Token" and copy the token value

## Project Structure

```
📦 Plexboxd
├─ /data                            # Stores persistent data
│  └─ movie_data.json               # Cached movie data and watch history
├─ /logs                            # Log files directory
│  ├─ letterboxd_integration.log    # Letterboxd integration logs
│  └─ plex_bot.log                  # Main bot logs
├─ /src                             # Source code
│  ├─ letterboxd_integration.py     # Letterboxd API interaction and logging
│  ├─ logging_config.py             # Logging configuration for the bot
│  ├─ plex_bot.py                   # Main bot code and Plex monitoring
│  ├─ utils.py                      # Utility functions for Discord embeds
│  └─ views.py                      # Discord UI components (buttons, selects)
├─ .env                             # Environment variables (sensitive data)
├─ .env.example                     # Example environment variables file
├─ .gitignore                       # Git ignore configuration
└─ requirements.txt                 # Python dependencies
```

## How It Works

1. **Plex Monitoring**: The bot connects to your Plex server and periodically checks for recently watched movies.

2. **Discord Notifications**: When a movie is watched, the bot sends an embed with all necessary movie information to the configured Discord channel.

   ![Movie Notification](https://i.imgur.com/4FbqqRk.png)

3. **Rating Interface**: Each notification includes a dropdown menu to rate the movie directly from Discord.

   ![Rating Interface](https://i.imgur.com/9cOsJwx.png)

4. **Letterboxd Integration**: When you rate a movie, the bot:
   - Logs into your Letterboxd account using `requests`
   - Searches for the movie using Selenium (headless)
   - Adds it to your diary with the correct date and rating via `requests`
   - Confirms the action with a success message

   ![Rating Confirmation](https://i.imgur.com/ukaeAVI.png)

5. **Date Assignment Logic**: The bot uses the `DATE_THRESHOLD_HOUR` setting to determine if late-night watches count for the current or previous day in your Letterboxd diary.

## Logging

The bot maintains two primary log files:

1. **plex_bot.log**
   - Bot startup and initialization
   - Plex connection status
   - Movie detection events
   - Discord notification events
   - Error messages

2. **letterboxd_integration.log**
   - Letterboxd login attempts
   - Movie search operations
   - Diary entry creation
   - Date assignment decisions

Both logs can also be forwarded to a Discord channel using the `DISCORD_LOGGING_WEBHOOK_URL` for easier monitoring.

## Troubleshooting

### Common Issues

1. **Bot can't connect to Plex**
   - Verify your Plex server is running
   - Check your `PLEX_SERVER_URL` and `PLEX_TOKEN` in the `.env` file
   - Ensure your firewall allows connections to your Plex server

2. **Letterboxd integration fails**
   - Verify your Letterboxd credentials
   - Check if Letterboxd is experiencing downtime
   - Ensure you have Chrome installed for Selenium (used for movie search)

3. **Discord notifications aren't showing up**
   - Verify your bot has proper permissions in the Discord server
   - Check if the `NOTIFY_CHANNEL_ID` is correct
   - Make sure the bot has access to the notification channel

### Checking Logs

- Review the log files in the `/logs` directory for detailed error information
- If you've configured Discord logging, check the logging channel for real-time updates

## Security Notes

- Your Letterboxd credentials and other sensitive information are stored in the `.env` file. Keep this file secure and never commit it to version control.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgements

- [Plex](https://www.plex.tv) for providing an excellent media server platform that makes tracking watched movies possible.
- [Letterboxd](https://letterboxd.com) for their movie logging and rating service, which inspired the core integration of this bot.
- [Discord](https://discord.com) for their robust API and bot framework, enabling seamless notifications and interactions.
- [PlexAPI](https://github.com/pkkid/python-plexapi) - A Python library for interacting with Plex servers, heavily utilized in this project for movie tracking.
- [Selenium](https://www.selenium.dev) - Used for headless browser automation to search for movies on Letterboxd.
- [discord.py](https://github.com/Rapptz/discord.py) - The backbone of the Discord bot functionality, making embeds and interactive components possible.

## Disclaimer

This project is not affiliated with or endorsed by Plex, Letterboxd, or Discord. It is an independent tool built for personal use and shared for educational purposes. Use it at your own risk and be respectful of the respective APIs by avoiding excessive requests. The developers are not responsible for any issues arising from misuse, including potential account restrictions by the integrated services.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
