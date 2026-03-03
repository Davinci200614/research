"""
Instagram engagement-rate scraper via TrendHero.

Handles reCAPTCHA solving (checkbox → audio challenge → manual fallback)
and result extraction.

Extracted from the original ``scrape_engagement.py``.
"""

import logging
import os
import platform
import re
import subprocess
import tempfile
import time
import unicodedata
from typing import Optional

import imageio_ffmpeg
import requests
import speech_recognition as sr
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

TRENDHERO_URL = "https://trendhero.io/engagement-rate-calculator-instagram/"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _detect_chrome_major() -> int:
    """Return the major version of the locally-installed Chrome browser.

    Tries (in order):
    1. Windows registry (fastest, most reliable).
    2. ``google-chrome --version`` on Linux / macOS.

    Returns 0 if detection fails (let uc fall back to its own logic).
    """
    # --- Windows registry ---------------------------------------------------
    if platform.system() == "Windows":
        import winreg  # noqa: WPS433 (only on Windows)

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for sub in (
                r"SOFTWARE\Google\Chrome\BLBeacon",
                r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon",
            ):
                try:
                    key = winreg.OpenKey(root, sub)
                    ver, _ = winreg.QueryValueEx(key, "version")
                    winreg.CloseKey(key)
                    major = int(str(ver).split(".")[0])
                    logger.info("Detected Chrome %s (major=%d) from registry", ver, major)
                    return major
                except (OSError, ValueError, IndexError):
                    continue

    # --- Linux / macOS CLI --------------------------------------------------
    for cmd in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        try:
            out = subprocess.check_output([cmd, "--version"], stderr=subprocess.DEVNULL, text=True)
            m = re.search(r"(\d+)", out)
            if m:
                major = int(m.group(1))
                logger.info("Detected Chrome major=%d from `%s --version`", major, cmd)
                return major
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    logger.warning("Could not detect Chrome version — letting uc auto-detect")
    return 0
MAX_CAPTCHA_RETRIES = 3

# ── IG username mapping (display name → handle) ─────────────────────────────

IG_USERNAMES = {
    "beyoncé": "beyonce",
    "beyonce": "beyonce",
    "justin bieber": "justinbieber",
    "ariana grande": "arianagrande",
    "taylor swift": "taylorswift",
    "ed sheeran": "teddysphotos",
    "the weeknd": "theweeknd",
    "drake": "champagnepapi",
    "adele": "adele",
    "bruno mars": "brunomars",
}


def get_ig_username(artist_name: str) -> str:
    """Convert artist display name to Instagram username."""
    key = artist_name.strip().lower()
    if key in IG_USERNAMES:
        return IG_USERNAMES[key]
    normalized = unicodedata.normalize("NFD", key)
    stripped = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", stripped)


# ── CAPTCHA helpers ──────────────────────────────────────────────────────────


def _is_captcha_solved(driver) -> bool:
    driver.switch_to.default_content()
    try:
        token = driver.execute_script(
            "return document.querySelector("
            "'textarea[name=\"g-recaptcha-response\"]')?.value || '';"
        )
        return bool(token)
    except Exception:
        return False


def _switch_to_iframe(driver, src_keyword: str):
    driver.switch_to.default_content()
    iframe = driver.find_element(
        By.CSS_SELECTOR, f"iframe[src*='{src_keyword}']"
    )
    driver.switch_to.frame(iframe)


def _transcribe_audio(audio_url: str) -> str:
    tmp = tempfile.mkdtemp()
    mp3 = os.path.join(tmp, "audio.mp3")
    wav = os.path.join(tmp, "audio.wav")
    try:
        with open(mp3, "wb") as f:
            f.write(requests.get(audio_url, timeout=15).content)
        subprocess.run(
            [FFMPEG, "-y", "-i", mp3, "-ar", "16000", "-ac", "1", "-f", "wav", wav],
            capture_output=True,
            timeout=30,
        )
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav) as src:
            return recognizer.recognize_google(recognizer.record(src))
    finally:
        for p in (mp3, wav):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def solve_captcha(driver) -> bool:
    """Solve reCAPTCHA: checkbox → audio → manual fallback."""
    driver.switch_to.default_content()
    if not driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']"):
        logger.info("No CAPTCHA detected")
        return True

    logger.info("CAPTCHA detected — solving…")

    # Click checkbox
    try:
        driver.switch_to.default_content()
        iframe = driver.find_element(
            By.CSS_SELECTOR, "iframe[src*='recaptcha/api2/anchor']"
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", iframe
        )
        time.sleep(1)
        driver.switch_to.frame(iframe)
        checkbox = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "recaptcha-anchor"))
        )
        driver.execute_script("arguments[0].click();", checkbox)
        time.sleep(2)
    except Exception as e:
        logger.warning("Could not click checkbox: %s", e)
        driver.switch_to.default_content()
        return False

    if _is_captcha_solved(driver):
        logger.info("CAPTCHA solved (checkbox only)")
        return True

    # Audio challenge loop
    for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
        logger.info("Audio attempt %d/%d", attempt, MAX_CAPTCHA_RETRIES)
        try:
            _switch_to_iframe(driver, "recaptcha/api2/bframe")
            time.sleep(1)
            try:
                WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "recaptcha-audio-button"))
                ).click()
                time.sleep(3)
            except Exception:
                pass

            _switch_to_iframe(driver, "recaptcha/api2/bframe")
            link = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".rc-audiochallenge-tdownload-link")
                )
            )
            audio_url = link.get_attribute("href")
            if not audio_url:
                audio_url = driver.find_element(
                    By.CSS_SELECTOR, "audio source"
                ).get_attribute("src")

            text = _transcribe_audio(audio_url)
            logger.info("Transcribed: '%s'", text)

            _switch_to_iframe(driver, "recaptcha/api2/bframe")
            driver.find_element(By.ID, "audio-response").send_keys(text)
            time.sleep(0.5)
            driver.find_element(By.ID, "recaptcha-verify-button").click()
            time.sleep(3)

            if _is_captcha_solved(driver):
                logger.info("CAPTCHA solved (audio)")
                driver.switch_to.default_content()
                return True

            _switch_to_iframe(driver, "recaptcha/api2/bframe")
            driver.find_element(By.ID, "recaptcha-reload-button").click()
            time.sleep(2)
        except Exception as e:
            logger.warning("Audio attempt failed: %s", e)

    # Manual fallback
    logger.info("Auto-solve failed — waiting 120 s for manual solve…")
    driver.switch_to.default_content()
    for _ in range(60):
        if _is_captcha_solved(driver):
            logger.info("CAPTCHA solved manually")
            return True
        time.sleep(2)
    logger.warning("CAPTCHA timeout")
    return False


# ── Click Check button ───────────────────────────────────────────────────────


def _click_check(driver):
    driver.switch_to.default_content()
    time.sleep(1)
    strategies = [
        lambda: WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(
                (
                    By.CSS_SELECTOR,
                    "#er-calculator > div > div.MuiCardContent-root-34.jss337"
                    " > form > div > div > div.MuiGrid-root-82.MuiGrid-item-84"
                    ".MuiGrid-grid-xs-12-128.MuiGrid-grid-sm-auto-129 > span > button",
                )
            )
        ).click(),
        lambda: driver.find_element(
            By.CSS_SELECTOR, "#er-calculator form button"
        ).click(),
        lambda: driver.find_element(
            By.XPATH, "//button[contains(text(),'Check')]"
        ).click(),
        lambda: driver.execute_script(
            "(document.querySelector('#er-calculator form button')"
            "||document.querySelector('#er-calculator button')).click();"
        ),
    ]
    for i, fn in enumerate(strategies, 1):
        try:
            fn()
            logger.info("Clicked Check (strategy %d)", i)
            return
        except Exception:
            pass
    logger.warning("Could not click Check button")


# ── Extract ER ───────────────────────────────────────────────────────────────


def _extract_er(driver) -> Optional[str]:
    for _ in range(8):
        time.sleep(5)
        try:
            val = driver.execute_script(
                """
                const el = document.querySelector('#er-calculator');
                if (!el) return null;
                const m = el.innerText.match(
                    /(\\d+\\.?\\d*)\\s*%\\s*\\n?\\s*Engagement Rate/i
                );
                return m ? m[1] + '%' : null;
                """
            )
            if val:
                return val
        except Exception:
            pass
        try:
            text = driver.find_element(
                By.CSS_SELECTOR, "#er-calculator"
            ).text
            matches = re.findall(r"(\d+\.?\d*)\s*%", text)
            if matches:
                return matches[0] + "%"
        except Exception:
            pass
    return None


# ── Public API ───────────────────────────────────────────────────────────────


def get_engagement_rate(
    ig_username: str,
    chrome_version: int = 0,
) -> Optional[str]:
    """
    Launch an undetected Chrome instance, navigate to TrendHero,
    solve CAPTCHA, and return the engagement rate string (e.g. ``"4.23%"``).

    A **visible** browser is used because headless Chrome is blocked by
    reCAPTCHA.  If *chrome_version* is ``0``, the installed Chrome version
    is detected automatically from the system.
    """
    logger.info("Fetching ER for @%s", ig_username)

    ver = chrome_version or _detect_chrome_major()

    opts = uc.ChromeOptions()
    opts.add_argument("--start-maximized")
    kw: dict = {"options": opts}
    if ver:
        kw["version_main"] = ver
    driver = uc.Chrome(**kw)

    try:
        driver.get(TRENDHERO_URL)
        time.sleep(3)

        search = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#er-calculator input")
            )
        )
        search.clear()
        time.sleep(0.5)
        search.send_keys(ig_username)
        time.sleep(1)

        if not solve_captcha(driver):
            logger.warning("CAPTCHA failed for @%s", ig_username)
            return None

        driver.switch_to.default_content()
        time.sleep(1)
        _click_check(driver)

        er = _extract_er(driver)
        if er:
            logger.info("ER for @%s: %s", ig_username, er)
        else:
            logger.warning("Could not extract ER for @%s", ig_username)
        return er
    finally:
        try:
            driver.quit()
        except OSError:
            pass


def _make_uc_driver(ver: int) -> uc.Chrome:
    """Create a new undetected-chromedriver instance."""
    opts = uc.ChromeOptions()
    opts.add_argument("--start-maximized")
    kw: dict = {"options": opts}
    if ver:
        kw["version_main"] = ver
    return uc.Chrome(**kw)


def _safe_quit(driver):
    """Quit driver, ignoring errors."""
    if driver:
        try:
            driver.quit()
        except Exception:
            pass


def get_engagement_rate_batch(
    ig_usernames: list[str],
    chrome_version: int = 0,
    max_restarts: int = 3,
) -> dict[str, Optional[str]]:
    """
    Fetch engagement rates for multiple IG usernames.

    Uses a single browser session where possible, but **automatically
    restarts Chrome** if the window crashes (up to *max_restarts* times
    per username). If *chrome_version* is ``0``, the installed Chrome
    version is detected automatically from the system.
    """
    results: dict[str, Optional[str]] = {}
    ver = chrome_version or _detect_chrome_major()
    driver = _make_uc_driver(ver)

    for username in ig_usernames:
        logger.info("Fetching ER for @%s", username)
        success = False

        for attempt in range(1, max_restarts + 1):
            try:
                driver.get(TRENDHERO_URL)
                time.sleep(3)

                search = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "#er-calculator input")
                    )
                )
                search.clear()
                time.sleep(0.5)
                search.send_keys(username)
                time.sleep(1)

                if not solve_captcha(driver):
                    logger.warning("CAPTCHA failed for @%s", username)
                    results[username] = None
                    success = True  # don't retry — CAPTCHA issue, not crash
                    break

                driver.switch_to.default_content()
                time.sleep(1)
                _click_check(driver)

                er = _extract_er(driver)
                results[username] = er
                if er:
                    logger.info("ER for @%s: %s", username, er)
                else:
                    logger.warning("No ER for @%s", username)
                success = True
                break

            except Exception as e:
                logger.warning(
                    "Attempt %d/%d for @%s failed: %s",
                    attempt, max_restarts, username, e,
                )
                # Browser likely crashed — restart it
                _safe_quit(driver)
                if attempt < max_restarts:
                    logger.info("Restarting Chrome…")
                    time.sleep(2)
                    try:
                        driver = _make_uc_driver(ver)
                    except Exception as restart_err:
                        logger.error("Chrome restart failed: %s", restart_err)
                        results[username] = None
                        success = True
                        break

        if not success:
            results[username] = None

        time.sleep(2)

    _safe_quit(driver)
    return results
