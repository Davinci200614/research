"""
Soundcharts scraper — login, search, extract follower data & IG username.

Extracted from the original ``soundchart.py`` / ``soundchart_live.py``.
"""

import logging
import re
import time
import urllib.parse
from typing import Dict, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

LOGIN_URL = "https://app.soundcharts.com/login"
SEARCH_URL = "https://app.soundcharts.com/app/search?page=all&search="


class SoundchartsScraper:
    """Manage a browser session for scraping Soundcharts artist data."""

    def __init__(self, email: str, password: str, headless: bool = True):
        self.email = email
        self.password = password
        self.headless = headless
        self.driver = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self):
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        self.driver = webdriver.Chrome(options=options)
        logger.info(
            "Chrome started (headless=%s)", self.headless
        )

    def stop(self):
        if self.driver:
            try:
                self.driver.quit()
            except OSError:
                pass
            self.driver = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    # ── Login ────────────────────────────────────────────────────────────

    def login(self) -> bool:
        logger.info("Navigating to Soundcharts login…")
        self.driver.get(LOGIN_URL)
        time.sleep(3)

        email_input = WebDriverWait(self.driver, 15).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        email_input.clear()
        email_input.send_keys(self.email)
        time.sleep(0.5)

        password_input = self.driver.find_element(By.NAME, "password")
        password_input.clear()
        password_input.send_keys(self.password)
        time.sleep(0.5)

        try:
            btn = self.driver.find_element(
                By.XPATH,
                "//*[@id='root']/div[2]/div/div/div[2]/div[1]/form/button",
            )
            btn.click()
            logger.info("Clicked 'Sign in' button")
        except Exception:
            password_input.send_keys(Keys.RETURN)
            logger.info("Pressed Enter to submit login")

        for _ in range(30):
            time.sleep(2)
            if "/login" not in self.driver.current_url:
                logger.info("Logged in: %s", self.driver.current_url)
                return True

        logger.warning("Login may have failed — still on login page")
        return False

    # ── Search ───────────────────────────────────────────────────────────

    def search_artist(self, artist_name: str) -> bool:
        """Navigate to an artist's Soundcharts profile via search."""
        logger.info("Searching for: %s", artist_name)
        encoded = urllib.parse.quote(artist_name.upper())
        self.driver.get(f"{SEARCH_URL}{encoded}")
        time.sleep(5)

        try:
            link = self.driver.find_element(
                By.XPATH,
                "//*[@id='root']/div[2]/div/div[1]/div[3]"
                "/div/div/div[1]/div[2]/a[1]/div/div[1]/img",
            )
        except Exception:
            logger.warning("No result found for '%s'", artist_name)
            return False

        href = (
            link.get_attribute("title")
            or link.get_attribute("alt")
            or link.text
        )
        logger.info("Found artist link: %s", href)
        link.click()
        time.sleep(5)

        try:
            WebDriverWait(self.driver, 15).until(
                EC.url_contains("/app/artist/")
            )
            logger.info("On artist profile: %s", self.driver.current_url)
            return True
        except Exception:
            return "/app/artist/" in self.driver.current_url

    # ── Extract follower data ────────────────────────────────────────────

    def extract_follower_data(self) -> Dict[str, str]:
        """Parse follower counts + genre from the artist overview page."""
        time.sleep(3)
        data: Dict[str, str] = {}

        # Make sure we're on Overview
        try:
            tab = self.driver.find_element(
                By.XPATH,
                "//a[contains(text(),'Overview')] | "
                "//button[contains(text(),'Overview')]",
            )
            tab.click()
            time.sleep(2)
        except Exception:
            pass

        try:
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            logger.warning("Could not read page text")
            return data

        patterns = {
            "tiktok_followers": r"TIKTOK FOLLOWERS\s*\n?\s*([\d.,]+[KMB]?)",
            "spotify_followers": r"SPOTIFY FOLLOWERS\s*\n?\s*([\d.,]+[KMB]?)",
            "instagram_followers": r"INSTAGRAM FOLLOWERS\s*\n?\s*([\d.,]+[KMB]?)",
            "bandsintown_followers": r"BANDSINTOWN FOLLOWERS\s*\n?\s*([\d.,]+[KMB]?)",
        }
        for key, pat in patterns.items():
            m = re.search(pat, page_text, re.IGNORECASE)
            if m:
                data[key] = m.group(1)

        # Genre
        genre_match = re.search(
            r"Genres\s*\n?\s*([^\n]+)", page_text, re.IGNORECASE
        )
        genre_val = genre_match.group(1).strip() if genre_match else ""
        if genre_val.lower().startswith("sub genre"):
            genre_val = ""
        if genre_val:
            data["genre"] = genre_val
        else:
            sub = re.search(
                r"Sub Genres\s*\n?\s*([^\n]+)", page_text, re.IGNORECASE
            )
            if sub and sub.group(1).strip():
                data["genre"] = sub.group(1).strip()

        if data:
            logger.info("Extracted: %s", data)
        else:
            logger.warning("No follower data found on page")
        return data

    # ── Extract IG username ──────────────────────────────────────────────

    def extract_ig_username(self) -> Optional[str]:
        """Find the Instagram username from the artist profile sources."""
        time.sleep(2)

        strategies = [
            lambda: self.driver.find_element(
                By.XPATH,
                "//*[@id='root']//a[contains(@href,'instagram.com')]",
            ),
            lambda: self.driver.find_element(
                By.XPATH,
                "//div[contains(@class,'source') or contains(@class,'Source')]"
                "//a[contains(@href,'instagram.com')]",
            ),
            lambda: self.driver.find_element(
                By.CSS_SELECTOR, "a[href*='instagram.com']"
            ),
            lambda: self.driver.execute_script(
                """
                const links = document.querySelectorAll(
                    'a[href*="instagram.com"]'
                );
                for (const link of links) {
                    if (!link.closest('nav, aside')) return link;
                }
                return links.length > 0 ? links[0] : null;
                """
            ),
        ]

        for fn in strategies:
            try:
                el = fn()
                if not el:
                    continue
                href = el.get_attribute("href")
                if not href or "instagram.com" not in href:
                    continue
                m = re.search(r"instagram\.com/([^/?&#]+)", href)
                if m:
                    username = m.group(1).strip("/")
                    skip = {"", "p", "explore", "accounts", "about"}
                    if username.lower() not in skip:
                        logger.info("IG username: @%s", username)
                        return username
            except Exception:
                continue

        logger.warning("No Instagram link found on profile")
        return None

    # ── Public: process a single artist ──────────────────────────────────

    def process_artist(
        self, artist_name: str
    ) -> Tuple[Dict[str, str], Optional[str], str]:
        """
        Search → profile → extract followers + IG username.

        Returns
        -------
        (follower_data, ig_username, soundcharts_url)
        """
        if not self.search_artist(artist_name):
            return {}, None, ""

        sc_url = self.driver.current_url
        follower_data = self.extract_follower_data()
        ig_username = self.extract_ig_username()
        return follower_data, ig_username, sc_url
