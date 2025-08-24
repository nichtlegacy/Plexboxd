# src/letterboxd_integration.py
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from dotenv import load_dotenv
from logging_config import DiscordHandler

# Load environment variables
load_dotenv()
USERNAME = os.getenv("LETTERBOXD_USERNAME")
PASSWORD = os.getenv("LETTERBOXD_PASSWORD")
LOGIN_URL = "https://letterboxd.com/user/login.do"
DIARY_URL = "https://letterboxd.com/s/save-diary-entry"
DISCORD_LOGGING_WEBHOOK_URL = os.getenv("DISCORD_LOGGING_WEBHOOK_URL")
DATE_THRESHOLD_HOUR = int(os.getenv("DATE_THRESHOLD_HOUR", 7))

def setup_logging():
    """Set up logging with file, console, and Discord handlers."""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    main_file_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, 'letterboxd_integration.log'),
        when='midnight',
        interval=1,
        backupCount=7,
        encoding='utf-8',
        utc=True
    )
    main_file_handler.setFormatter(formatter)
    main_file_handler.suffix = "%Y-%m-%d"
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger = logging.getLogger('LetterboxdIntegration')
    logger.setLevel(logging.INFO)
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(main_file_handler)
    logger.addHandler(console_handler)
    
    if DISCORD_LOGGING_WEBHOOK_URL:
        discord_handler = DiscordHandler(
            webhook_url=DISCORD_LOGGING_WEBHOOK_URL,
            bot_name="Letterboxd Bot Logging",
            title_prefix="Letterboxd Integration"
        )
        discord_handler.setFormatter(formatter)
        discord_handler.setLevel(logging.INFO)
        logger.addHandler(discord_handler)
    
    return logger

logger = setup_logging()

def login(session):
    """Perform login to Letterboxd and return CSRF token."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Referer": "https://letterboxd.com/",
    }
    logger.info("Starting login process...")
    response = session.get("https://letterboxd.com/sign-in/", headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    csrf_token = soup.find("input", {"name": "__csrf"})["value"] if soup.find("input", {"name": "__csrf"}) else ""
    
    if not csrf_token:
        logger.error("CSRF token not found!")
        raise ValueError("CSRF token not found")

    login_data = {
        "username": USERNAME,
        "password": PASSWORD,
        "__csrf": csrf_token,
        "authenticationCode": ""
    }
    response = session.post(LOGIN_URL, data=login_data, headers=headers, allow_redirects=True)
    
    try:
        login_response = json.loads(response.text)
        if login_response.get("result") != "success":
            logger.error(f"Letterboxd login failed: {login_response.get('messages', 'Unknown error')}")
            raise ValueError(f"Letterboxd login failed: {login_response.get('messages', 'Unknown error')}")
        logger.info("Successfully logged in to Letterboxd")
    except json.JSONDecodeError:
        logger.error("Invalid JSON response during Letterboxd login")
        raise ValueError("Invalid JSON response during Letterboxd login")
    
    return csrf_token

def get_film_id_selenium(session, film_name, film_year, original_title=None, tmdb_id=None):
    """Retrieve film ID from Letterboxd using TMDb ID or search.
    
    Args:
        session: The requests session with authentication.
        film_name: The name of the film to search for.
        film_year: The release year of the film.
        original_title: Optional original title of the film.
        tmdb_id: Optional TMDb ID for direct lookup.
        
    Returns:
        str: The Letterboxd film ID if found, None otherwise.
    """
    search_title = original_title if original_title else film_name
    logger.info(f"Searching for film: {search_title} ({film_year}) with TMDb ID: {tmdb_id}")

    # Try TMDb ID approach first if available
    if tmdb_id:
        try:
            tmdb_url = f"https://letterboxd.com/tmdb/{tmdb_id}"
            logger.info(f"Attempting to fetch film ID via TMDb URL: {tmdb_url}")
            headers = {
                "User-Agent": session.headers['User-Agent'],
                "Referer": "https://letterboxd.com/",
            }
            response = session.get(tmdb_url, headers=headers, allow_redirects=True)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                # Look for any element with data-film-id attribute (new structure)
                film_element = soup.find(attrs={"data-film-id": True})
                if film_element:
                    film_id = film_element['data-film-id']
                    logger.info(f"Film ID found via TMDb ID: {film_id}")
                    return film_id
                else:
                    logger.warning(f"No film ID found at TMDb URL: {tmdb_url}, falling back to search")
            else:
                logger.warning(f"TMDb URL request failed with status {response.status_code}, falling back to search")
        except Exception as e:
            logger.error(f"Error fetching film ID via TMDb ID: {str(e)}, falling back to search")

    # Fallback to existing Selenium-based search
    logger.info(f"Using fallback search for: {search_title} ({film_year})")
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(f"user-agent={session.headers['User-Agent']}")

    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        driver.get("https://letterboxd.com")
        for cookie in session.cookies:
            driver.add_cookie({
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
            })
        
        search_query = f"{search_title} {film_year}".replace(" ", "+")
        search_url = f"https://letterboxd.com/search/films/{search_query}/"
        logger.debug(f"Opening search: {search_url}")
        driver.get(search_url)
        
        wait = WebDriverWait(driver, 10)
        if "/film/" in driver.current_url:
            logger.info(f"Redirected to film page: {driver.current_url}")
            # Try to find film ID on the page - check both new and old structures
            film_id = None

            # First try the new structure
            try:
                film_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-film-id]")))
                film_id = film_elem.get_attribute("data-film-id")
                logger.info(f"Film ID found via data-film-id: {film_id}")
            except:
                # Fallback to old structure
                try:
                    film_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.film-poster")))
                    film_id = film_elem.get_attribute("data-film-id")
                    logger.info(f"Film ID found via film-poster: {film_id}")
                except:
                    logger.warning("Could not find film ID on direct film page")

            return film_id
        
        # Look for elements with data-film-id attribute (the new structure)
        film_elements = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "[data-film-id]")))
        logger.info(f"Found {len(film_elements)} elements with data-film-id attribute")

        if film_elements:
            first_film_element = film_elements[0]
            film_id = first_film_element.get_attribute("data-film-id")
            logger.info(f"Selected first film ID: {film_id}")
            return film_id
        
        logger.warning(f"Film ID for '{search_title} ({film_year})' not found!")
        return None
        
    except Exception as e:
        logger.error(f"Error retrieving film ID via search: {str(e)}")
        return None
    finally:
        driver.quit()

def save_diary_entry(session, csrf_token, film_id, rating, viewing_date=None):
    """Save a diary entry with rating and adjusted date.
    
    Args:
        session: The requests session with authentication.
        csrf_token: The CSRF token for the request.
        film_id: The Letterboxd film ID.
        rating: The rating value (0.5-5.0).
        viewing_date: Optional ISO format date string.
    
    Raises:
        ValueError: If the diary entry fails or response is invalid.
    """
    if viewing_date:
        viewing_date = datetime.fromisoformat(viewing_date)
        if viewing_date.hour < DATE_THRESHOLD_HOUR:
            viewing_date = viewing_date - timedelta(days=1)
            logger.info(f"Viewing time {viewing_date.strftime('%H:%M')} is before {DATE_THRESHOLD_HOUR}:00, adjusted date to {viewing_date.strftime('%Y-%m-%d')}")
    else:
        viewing_date = get_adjusted_date()

    """Save a diary entry with rating and adjusted date."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Referer": "https://letterboxd.com/",
        "Origin": "https://letterboxd.com",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    diary_data = {
        "json": "true",
        "__csrf": csrf_token,
        "viewingId": "",
        "filmId": film_id,
        "specifiedDate": "true",
        "viewingDateStr": viewing_date.strftime("%Y-%m-%d"),
        "review": "",
        "tags": "",
        "rating": int(rating * 2),  # Convert to 1-10 scale
        "liked": "false",
        "reviewLanguageCodeHint": "de-DE"
    }
    logger.info(f"Saving diary entry for film ID {film_id} with rating {rating} for date {viewing_date.strftime('%Y-%m-%d')}")
    response = session.post(DIARY_URL, data=diary_data, headers=headers)
    
    try:
        diary_response = json.loads(response.text)
        if diary_response.get("result") is True:
            logger.info(f"Successfully saved diary entry with {rating} stars")
        else:
            error_msg = diary_response.get('messages', 'Unknown error')
            logger.error(f"Failed to save diary entry: {error_msg}")
            raise ValueError(f"Failed to save diary entry: {error_msg}")
    except json.JSONDecodeError:
        logger.error("Invalid JSON response while saving diary entry")
        raise ValueError("Invalid JSON response while saving diary entry")

def get_adjusted_date():
    """Return adjusted date: previous day if before the configured threshold hour."""
    now = datetime.now()
    threshold_hour = DATE_THRESHOLD_HOUR
    if now.hour < threshold_hour:
        adjusted_date = now - timedelta(days=1)
        logger.info(f"Time {now.strftime('%H:%M')} is before {threshold_hour}:00, setting date to previous day {adjusted_date.strftime('%Y-%m-%d')}")
        return adjusted_date
    logger.info(f"Time {now.strftime('%H:%M')} is after {threshold_hour}:00, keeping date as {now.strftime('%Y-%m-%d')}")
    return now