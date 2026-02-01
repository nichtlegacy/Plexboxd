# src/letterboxd_integration.py
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import json
import pickle
import time
import undetected_chromedriver as uc
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

COOKIE_FILE = os.path.join("data", "cookies.pkl")

def save_cookies_from_driver(driver, csrf_token):
    """Save session cookies from Selenium driver to file."""
    try:
        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
        cookies_dict = {c['name']: c['value'] for c in driver.get_cookies()}
        with open(COOKIE_FILE, 'wb') as f:
            pickle.dump({'cookies': cookies_dict, 'csrf_token': csrf_token}, f)
        logger.debug("Cookies and CSRF token saved to file")
    except Exception as e:
        logger.warning(f"Failed to save cookies: {e}")

def load_cookies_dict():
    """Load session cookies dict from file."""
    if not os.path.exists(COOKIE_FILE):
        return None
    try:
        with open(COOKIE_FILE, 'rb') as f:
            data = pickle.load(f)
            return data.get('cookies', {})
    except Exception as e:
        logger.warning(f"Failed to load cookies: {e}")
        return None

def create_driver():
    """Create a Chrome driver with correct options and fallbacks."""
    def get_options():
        options = uc.ChromeOptions()
        # options.add_argument("--headless=new")  # Disabled for Cloudflare bypass with Xvfb
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-accelerated-2d-canvas")
        options.add_argument("--window-size=1920,1080")
        return options

    try:
        # Try using the system driver first (usually matches installed Chromium)
        logger.debug("Attempting to create driver using system chromedriver...")
        # Check if running in Docker (Linux) or local (Windows) to adjust path
        driver_path = '/usr/bin/chromedriver' if os.name == 'posix' else None

        return uc.Chrome(options=get_options(), driver_executable_path=driver_path, version_main=None)
    except Exception as e:
        logger.warning(f"Failed to create driver with system executable: {e}. Retrying with auto-download (v144)...")
        try:
            # Fallback: try letting uc download, but create FRESH options object
            # We target 144 as that seems to be the current stable Chrome version on Debian
            return uc.Chrome(options=get_options(), version_main=144)
        except Exception as e2:
            logger.warning(f"Failed to create driver in fallback: {e2}. Retrying with auto-version...")
            # Last ditch: try without version specification
            return uc.Chrome(options=get_options())

def login(session):
    """Perform login to Letterboxd using Selenium and return CSRF token.

    Uses undetected-chromedriver to bypass Cloudflare protection.
    Tries to restore session from cookies first.
    """
    logger.info("Starting login process with undetected Chrome...")

    try:
        driver = create_driver()
    except Exception as e:
        logger.error(f"Failed to initialize Chrome driver: {str(e)}")
        raise

    try:
        # Set realistic viewport
        driver.set_window_size(1920, 1080)

        # 1. Navigate to domain first to set context for cookies
        logger.debug("Navigating to Letterboxd base page...")
        driver.get("https://letterboxd.com/")

        # 2. Try to load and inject cookies
        stored_cookies = load_cookies_dict()
        if stored_cookies:
            logger.info("Found stored cookies, injecting into browser...")
            for name, value in stored_cookies.items():
                try:
                    driver.add_cookie({'name': name, 'value': value})
                except Exception as e:
                    logger.debug(f"Could not add cookie {name}: {e}")

            # Refresh to apply cookies
            driver.refresh()
            time.sleep(2)

            # Check if we are logged in
            try:
                # Wait briefly for user menu
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".profile-menu, .has-icon-menu, .nav-account")))
                logger.info("Session restored successfully via cookies!")

                # Get CSRF and sync cookies to session
                page_source = driver.page_source
                soup = BeautifulSoup(page_source, "html.parser")
                csrf_token = soup.find("input", {"name": "__csrf"})["value"] if soup.find("input", {"name": "__csrf"}) else ""

                # Transfer cookies to requests session
                for cookie in driver.get_cookies():
                    session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))

                # Save fresh cookies
                save_cookies_from_driver(driver, csrf_token)

                return csrf_token
            except:
                logger.info("Restored cookies expired or invalid, proceeding to login...")

        # 3. If restore failed, do full login
        logger.debug("Navigating to Letterboxd sign-in page...")
        driver.get("https://letterboxd.com/sign-in/")

        # Wait for page to load completely
        time.sleep(2)

        # Wait for page to load and find login form
        wait = WebDriverWait(driver, 20)
        username_field = wait.until(EC.presence_of_element_located((By.ID, "field-username")))
        password_field = driver.find_element(By.ID, "field-password")

        # Fill in credentials
        logger.debug("Filling login credentials...")
        username_field.send_keys(USERNAME)
        password_field.send_keys(PASSWORD)

        # Try multiple selectors for the submit button
        submit_button = None
        submit_selectors = [
            "input[type='submit']",
            "button[type='submit']",
            ".submit",
            "input.button",
            "button.button",
            ".button.-action"
        ]

        for selector in submit_selectors:
            try:
                submit_button = driver.find_element(By.CSS_SELECTOR, selector)
                logger.debug(f"Found submit button with selector: {selector}")
                break
            except:
                continue

        if not submit_button:
            logger.error("Could not find submit button with any known selector")
            # Try submitting the form directly by pressing Enter on password field
            logger.debug("Attempting to submit by pressing Enter on password field")
            from selenium.webdriver.common.keys import Keys
            password_field.send_keys(Keys.RETURN)
        else:
            submit_button.click()

        # Wait for navigation after login (similar to letterboxd-graph's delay strategy)
        time.sleep(3)

        # Check for error messages
        try:
            error_element = driver.find_element(By.CSS_SELECTOR, ".error-message, .form-error, .notice.error")
            if error_element.is_displayed():
                error_msg = error_element.text
                logger.error(f"Login failed with error message: {error_msg}")
                raise ValueError(f"Letterboxd login failed: {error_msg}")
        except:
            # No error element found or not displayed - login likely successful
            pass

        # Verify we're logged in by checking for user-specific elements
        # (similar to how letterboxd-graph waits for specific selectors)
        try:
            # Wait for the user menu or account link to appear (indicates successful login)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".profile-menu, .has-icon-menu")))
            logger.info("Login verification successful - user menu detected")
        except:
            logger.warning("Could not verify login by user menu, proceeding anyway")

        # Additional delay for any dynamic content (matching letterboxd-graph's 1500ms delay)
        time.sleep(1.5)

        # Transfer cookies from Selenium to requests session
        logger.debug("Transferring cookies from Selenium to session...")
        for cookie in driver.get_cookies():
            session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))

        logger.info("Successfully logged in to Letterboxd via Selenium")

        # Get CSRF token from the page after login
        driver.get("https://letterboxd.com/")

        # Wait for page load
        time.sleep(1)

        page_source = driver.page_source
        soup = BeautifulSoup(page_source, "html.parser")
        csrf_token = soup.find("input", {"name": "__csrf"})["value"] if soup.find("input", {"name": "__csrf"}) else ""

        if not csrf_token:
            logger.error("CSRF token not found after login!")
            raise ValueError("CSRF token not found")

        logger.debug(f"CSRF token retrieved: {csrf_token[:10]}...")

        # Save cookies for next time
        save_cookies_from_driver(driver, csrf_token)

        return csrf_token

    except Exception as e:
        logger.error(f"Error during Selenium login: {str(e)}")
        raise
    finally:
        driver.quit()

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

    # Use Selenium for all requests to avoid Cloudflare 403 errors
    try:
        driver = create_driver()
    except Exception as e:
        logger.error(f"Failed to initialize Chrome driver: {str(e)}")
        # If we can't create a driver, we can't search
        return None

    try:
        # Load Letterboxd and transfer cookies
        driver.get("https://letterboxd.com")
        for cookie in session.cookies:
            cookie_dict = {
                'name': cookie.name,
                'value': cookie.value,
            }
            # Add optional fields if they exist
            if hasattr(cookie, 'domain') and cookie.domain:
                cookie_dict['domain'] = cookie.domain
            if hasattr(cookie, 'path') and cookie.path:
                cookie_dict['path'] = cookie.path

            try:
                driver.add_cookie(cookie_dict)
            except Exception as e:
                logger.debug(f"Could not add cookie {cookie.name}: {e}")
                continue

        # Try TMDb ID approach first if available (using Selenium to avoid 403)
        if tmdb_id:
            try:
                import time
                tmdb_url = f"https://letterboxd.com/tmdb/{tmdb_id}"
                logger.info(f"Fetching film ID via TMDb URL with Selenium: {tmdb_url}")

                driver.get(tmdb_url)

                # Wait longer for redirect (Docker environments may be slower)
                wait = WebDriverWait(driver, 15)
                time.sleep(5)  # Increased wait for redirect/dynamic content

                # Check if we were redirected to a film page
                current_url = driver.current_url
                logger.debug(f"Current URL after TMDb redirect: {current_url}")

                # Check page title to see if Cloudflare challenge is present
                page_title = driver.title
                logger.debug(f"Page title: {page_title}")

                # Log page source snippet to debug
                page_source = driver.page_source
                if "cloudflare" in page_source.lower() or "challenge" in page_source.lower():
                    logger.warning("Cloudflare challenge detected on TMDb page")
                    logger.debug(f"Page source preview: {page_source[:500]}")

                if "/film/" in current_url:
                    # We were redirected to the film page, try to find the film ID
                    try:
                        # Wait for an element with data-film-id to appear
                        film_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-film-id]")))
                        film_id = film_elem.get_attribute("data-film-id")
                        logger.info(f"Film ID found via TMDb ID using Selenium: {film_id}")
                        return film_id
                    except Exception as elem_error:
                        logger.warning(f"Could not find element with data-film-id: {elem_error}")
                        # Fallback: try parsing page source
                        page_source = driver.page_source
                        soup = BeautifulSoup(page_source, "html.parser")
                        film_element = soup.find(attrs={"data-film-id": True})
                        if film_element:
                            film_id = film_element['data-film-id']
                            logger.info(f"Film ID found via TMDb ID from page source: {film_id}")
                            return film_id
                        else:
                            logger.warning(f"No film ID found at TMDb URL, falling back to search")
                else:
                    logger.warning(f"TMDb URL did not redirect to film page (got {current_url}), falling back to search")
            except Exception as e:
                logger.error(f"Error fetching film ID via TMDb URL: {str(e)}, falling back to search")

        # Fallback to search
        logger.info(f"Using search for: {search_title} ({film_year})")

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

def save_diary_entry(session, csrf_token, film_id, rating, viewing_date=None, rewatch=False, liked=False, tags="", review=""):
    """Save a diary entry with rating and optional diary fields.
    
    Args:
        session: The requests session with authentication.
        csrf_token: The CSRF token for the request.
        film_id: The Letterboxd film ID.
        rating: The rating value (0.5-5.0).
        viewing_date: Optional ISO format date string.
        rewatch: Whether this is a rewatch (default False).
        liked: Whether the user liked the film (default False).
        tags: Comma-separated tags string (default empty).
        review: Review text (default empty).
    
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
        "review": review,
        "tags": tags,
        "rating": int(rating * 2),  # Convert to 1-10 scale
        "liked": "true" if liked else "false",
        "rewatch": "true" if rewatch else "false",
        "reviewLanguageCodeHint": "de-DE"
    }
    
    # Parse and add individual tags (Letterboxd expects multiple 'tag' fields)
    if tags:
        # Split by comma or space, strip whitespace, filter empty
        tag_list = [t.strip() for t in tags.replace(',', ' ').split() if t.strip()]
        for tag in tag_list:
            # Use list to allow multiple 'tag' keys
            if 'tag' not in diary_data:
                diary_data['tag'] = []
            if isinstance(diary_data.get('tag'), list):
                diary_data['tag'].append(tag)
            else:
                diary_data['tag'] = [diary_data['tag'], tag]
    
    logger.info(f"Saving diary entry for film ID {film_id} with rating {rating}, liked={liked}, rewatch={rewatch} for date {viewing_date.strftime('%Y-%m-%d')}")
    
    # Convert to proper format for requests (handle multiple tag values)
    post_data = []
    for key, value in diary_data.items():
        if isinstance(value, list):
            for v in value:
                post_data.append((key, v))
        else:
            post_data.append((key, value))
    
    response = session.post(DIARY_URL, data=post_data, headers=headers)
    
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