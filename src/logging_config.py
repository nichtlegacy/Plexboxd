# src/logging_config.py
import logging
import requests
import datetime
import time


class DiscordHandler(logging.Handler):
    """Custom handler to send logs to a Discord webhook."""
    def __init__(self, webhook_url, bot_name="Plexboxd Logging Bot", title_prefix="Plexboxd Monitor", max_retries=3):
        super().__init__()
        self.webhook_url = webhook_url
        self.bot_name = bot_name
        self.title_prefix = title_prefix
        self.max_retries = max_retries
        self.fallback_logger = logging.getLogger('DiscordHandlerFallback')
        self.fallback_logger.setLevel(logging.ERROR)
        if not self.fallback_logger.handlers:
            self.fallback_logger.addHandler(logging.StreamHandler())

    def emit(self, record):
        """Send log message to Discord with retry logic."""
        for attempt in range(self.max_retries):
            try:
                log_message = self.format(record)
                level = record.levelname
                color = {
                    "DEBUG": 0x7289DA,    # Blue
                    "INFO": 0x2ECC71,     # Green
                    "WARNING": 0xF1C40F,  # Yellow
                    "ERROR": 0xE74C3C,    # Red
                    "CRITICAL": 0x992D22  # Dark Red
                }.get(level, 0x7289DA)

                if len(log_message) > 1900:
                    log_message = log_message[:1900] + "... (truncated)"

                timestamp = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc).isoformat()
                data = {
                    "username": self.bot_name,
                    "embeds": [{
                        "title": f"{level} - {self.title_prefix}",
                        "description": f"```{log_message}```",
                        "color": color,
                        "timestamp": timestamp
                    }]
                }

                response = requests.post(self.webhook_url, json=data, timeout=5)
                if response.status_code == 204:
                    return
                elif response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    self.fallback_logger.warning(f"Rate limit hit, waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                else:
                    self.fallback_logger.error(f"Failed to send to Discord: {response.status_code} - {response.text}")
                    break
            except Exception as e:
                self.fallback_logger.error(f"Error in DiscordHandler: {e}")
                break
        else:
            self.fallback_logger.error("Max retries reached, message could not be sent.")