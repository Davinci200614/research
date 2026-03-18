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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

TRENDHERO_URL = "https://trendhero.io/engagement-rate-calculator-instagram/"
SOCIALCAT_URL = "https://thesocialcat.com/tools/instagram-engagement-rate-calculator"
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
ER_POLL_INTERVAL_SECONDS = 5
ER_MAX_POLLS = 16
ER_MAX_USER_ATTEMPTS = 3
ER_RETRY_BACKOFF_SECONDS = 3
SOCIALCAT_MAX_POLLS = 16
SOCIALCAT_POLL_INTERVAL_SECONDS = 2

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


def _get_bframe_context_id(driver) -> Optional[int]:
    """Return the CDP execution-context ID for the reCAPTCHA bframe.

    Uses ``Runtime.enable`` + ``Page.getFrameTree`` via CDP so we never need
    ``switch_to.frame()``, which crashes Chrome 115+ on cross-origin iframes.
    """
    try:
        tree = driver.execute_cdp_cmd("Page.getFrameTree", {})
    except Exception:
        return None

    # Find the bframe's frameId
    bframe_id = None

    def _walk(node):
        nonlocal bframe_id
        url = node.get("frame", {}).get("url", "")
        if "recaptcha/api2/bframe" in url:
            bframe_id = node["frame"]["id"]
            return
        for child in node.get("childFrames", []):
            _walk(child)

    _walk(tree["frameTree"])
    if not bframe_id:
        return None

    # Get the isolated world execution context for that frame
    try:
        driver.execute_cdp_cmd("Runtime.enable", {})
        contexts = driver.execute_cdp_cmd(
            "Runtime.evaluate",
            {"expression": "0", "contextId": 1},  # dummy to ensure enable
        )
    except Exception:
        pass

    # Create an isolated world in the bframe so we can execute JS
    try:
        result = driver.execute_cdp_cmd(
            "Page.createIsolatedWorld",
            {"frameId": bframe_id, "worldName": "captcha_helper"},
        )
        return result.get("executionContextId")
    except Exception:
        return None


def _cdp_eval(driver, context_id: int, expression: str):
    """Evaluate JS in the given CDP execution context and return the value."""
    result = driver.execute_cdp_cmd(
        "Runtime.evaluate",
        {
            "expression": expression,
            "contextId": context_id,
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    ex = result.get("exceptionDetails")
    if ex:
        raise RuntimeError(ex.get("text", str(ex)))
    return result.get("result", {}).get("value")


def _switch_to_iframe(driver, src_keyword: str, timeout: int = 15):
    """Switch into an iframe, waiting until it is fully loaded and accessible.

    Uses ``EC.frame_to_be_available_and_switch_to_it`` rather than a bare
    ``find_element`` + ``switch_to.frame`` pair, which crashes when the frame
    is mid-reload (e.g. after clicking the reCAPTCHA audio-challenge button).
    """
    driver.switch_to.default_content()
    WebDriverWait(driver, timeout).until(
        EC.frame_to_be_available_and_switch_to_it(
            (By.CSS_SELECTOR, f"iframe[src*='{src_keyword}']")
        )
    )


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

    # Audio challenge loop — uses CDP to interact with the bframe to avoid
    # the Chrome 115+ renderer crash on cross-origin switch_to.frame().
    for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
        logger.info("Audio attempt %d/%d", attempt, MAX_CAPTCHA_RETRIES)
        try:
            driver.switch_to.default_content()

            # Get CDP execution context for the bframe
            ctx = None
            for _ in range(10):
                ctx = _get_bframe_context_id(driver)
                if ctx:
                    break
                time.sleep(1)
            if not ctx:
                raise RuntimeError("Could not get bframe CDP context")

            # Click the audio button if present
            _cdp_eval(driver, ctx, """
                (() => {
                    const btn = document.getElementById('recaptcha-audio-button');
                    if (btn) btn.click();
                })()
            """)
            time.sleep(5)

            # Re-acquire context (bframe reloads after clicking audio button)
            ctx = None
            for _ in range(20):
                ctx = _get_bframe_context_id(driver)
                if ctx:
                    # Check if the audio challenge is loaded
                    ready = _cdp_eval(driver, ctx, """
                        !!(document.querySelector('.rc-audiochallenge-tdownload-link')
                           || document.querySelector('audio source'))
                    """)
                    if ready:
                        break
                    ctx = None
                time.sleep(1)
            if not ctx:
                raise RuntimeError("bframe not ready after audio button click")

            # Get audio URL
            audio_url = _cdp_eval(driver, ctx, """
                (() => {
                    const link = document.querySelector('.rc-audiochallenge-tdownload-link');
                    if (link && link.href) return link.href;
                    const src = document.querySelector('audio source');
                    return src ? src.src : null;
                })()
            """)
            if not audio_url:
                raise RuntimeError("No audio URL found")

            text = _transcribe_audio(audio_url)
            logger.info("Transcribed: '%s'", text)

            # Re-acquire context and type the answer
            ctx = _get_bframe_context_id(driver)
            if not ctx:
                raise RuntimeError("Lost bframe context before typing answer")

            # Escape single quotes in transcribed text for JS string
            safe_text = text.replace("\\", "\\\\").replace("'", "\\'")
            _cdp_eval(driver, ctx, f"""
                (() => {{
                    const input = document.getElementById('audio-response');
                    if (input) {{
                        input.value = '{safe_text}';
                        input.dispatchEvent(new Event('input', {{bubbles: true}}));
                    }}
                    const btn = document.getElementById('recaptcha-verify-button');
                    if (btn) btn.click();
                }})()
            """)
            time.sleep(3)

            if _is_captcha_solved(driver):
                logger.info("CAPTCHA solved (audio)")
                driver.switch_to.default_content()
                return True

            # Reload for next attempt
            ctx = _get_bframe_context_id(driver)
            if ctx:
                _cdp_eval(driver, ctx, """
                    (() => {
                        const btn = document.getElementById('recaptcha-reload-button');
                        if (btn) btn.click();
                    })()
                """)
            time.sleep(2)
        except Exception as e:
            logger.warning("Audio attempt failed: %s", e)

    # Manual fallback — shorter timeout in headless since no one can interact
    wait_secs = 30
    logger.info("Auto-solve failed — waiting %d s for possible deferred solve…", wait_secs)
    driver.switch_to.default_content()
    for _ in range(wait_secs // 2):
        if _is_captcha_solved(driver):
            logger.info("CAPTCHA solved (deferred)")
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


def _click_try_again(driver) -> bool:
    """Click TrendHero 'Try again' button when error panel is shown."""
    driver.switch_to.default_content()

    # Strategy 1: direct text match via XPath.
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//div[@id='er-calculator']//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'try again')]",
                )
            )
        )
        btn.click()
        logger.info("Clicked Try again (xpath)")
        return True
    except Exception:
        pass

    # Strategy 2: JS fallback scans buttons by text.
    try:
        clicked = driver.execute_script(
            """
            const buttons = document.querySelectorAll('#er-calculator button');
            for (const b of buttons) {
                const text = (b.innerText || '').trim().toLowerCase();
                if (text.includes('try again')) {
                    b.click();
                    return true;
                }
            }
            return false;
            """
        )
        if clicked:
            logger.info("Clicked Try again (js)")
            return True
    except Exception:
        pass

    return False


def _fill_username(driver, username: str) -> bool:
    """Fill TrendHero username input with multiple fallback strategies."""
    try:
        search = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "#er-calculator input"))
        )
    except Exception:
        return False

    # Strategy 1: regular clear + type
    try:
        search.click()
        time.sleep(0.2)
        search.clear()
        time.sleep(0.2)
        search.send_keys(username)
        time.sleep(0.5)
    except Exception:
        pass

    # Strategy 2: Ctrl+A + Backspace + type
    try:
        current_val = (search.get_attribute("value") or "").strip()
        if current_val != username:
            search.click()
            search.send_keys(Keys.CONTROL, "a")
            search.send_keys(Keys.BACKSPACE)
            time.sleep(0.2)
            search.send_keys(username)
            time.sleep(0.5)
    except Exception:
        pass

    # Strategy 3: JS value set + input/change events
    try:
        current_val = (search.get_attribute("value") or "").strip()
        if current_val != username:
            driver.execute_script(
                """
                const el = document.querySelector('#er-calculator input');
                if (!el) return false;
                el.value = arguments[0];
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
                """,
                username,
            )
            time.sleep(0.5)
    except Exception:
        pass

    try:
        final_val = (search.get_attribute("value") or "").strip()
        return final_val == username
    except Exception:
        return False


def _attempt_fetch_er(driver, username: str) -> Optional[str]:
    """Single end-to-end attempt for one username."""
    driver.get(TRENDHERO_URL)
    time.sleep(3)

    if not _fill_username(driver, username):
        logger.warning("Could not fill username for @%s", username)
        return None

    if not solve_captcha(driver):
        logger.warning("CAPTCHA failed for @%s", username)
        return None

    driver.switch_to.default_content()
    time.sleep(1)

    # CAPTCHA flow sometimes clears input; ensure it is still present.
    if not _fill_username(driver, username):
        logger.warning("Username missing after CAPTCHA for @%s", username)
        return None

    _click_check(driver)

    # Fallback submit if click handler was not triggered.
    try:
        search = driver.find_element(By.CSS_SELECTOR, "#er-calculator input")
        search.send_keys(Keys.RETURN)
    except Exception:
        pass

    return _extract_er(driver)


def _extract_socialcat_er(driver) -> Optional[str]:
    """Extract ER from SocialCat calculator result cards."""
    for _ in range(SOCIALCAT_MAX_POLLS):
        time.sleep(SOCIALCAT_POLL_INTERVAL_SECONDS)
        try:
            text = driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            text = ""

        # Prefer values tied to "Engagement Rate" to avoid matching page badges.
        for pattern in (
            r"Engagement\s*Rate[^\n\r]{0,120}?(\d{1,2}(?:\.\d{1,2})?\s*%)",
            r"(\d{1,2}(?:\.\d{1,2})?\s*%)\s*\n?\s*Engagement\s*Rate",
        ):
            m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(1).replace(" ", "")

        # DOM fallback: inspect candidate metric cards.
        try:
            val = driver.execute_script(
                r"""
                const nodes = Array.from(document.querySelectorAll('div,section,span,p,h2,h3,h4'));
                for (const n of nodes) {
                    const t = (n.innerText || '').trim();
                    if (!t) continue;
                    if (!/engagement\s*rate/i.test(t)) continue;
                    const m = t.match(/(\d{1,2}(?:\.\d{1,2})?\s*%)/);
                    if (m) return m[1].replace(/\s+/g, '');

                    const parent = n.closest('div,section');
                    if (parent) {
                        const ptxt = (parent.innerText || '').trim();
                        const pm = ptxt.match(/(\d{1,2}(?:\.\d{1,2})?\s*%)\s*[\s\S]{0,80}Engagement\s*Rate/i)
                            || ptxt.match(/Engagement\s*Rate[\s\S]{0,80}(\d{1,2}(?:\.\d{1,2})?\s*%)/i);
                        if (pm) return pm[1].replace(/\s+/g, '');
                    }
                }
                return null;
                """
            )
            if val:
                return val
        except Exception:
            pass

    return None


def _attempt_fetch_er_socialcat(driver, username: str) -> Optional[str]:
    """Fallback ER extraction from SocialCat calculator."""
    logger.info("Trying SocialCat fallback for @%s", username)

    driver.get(SOCIALCAT_URL)
    time.sleep(3)

    # Accept handle or URL format.
    query = username if username.startswith("@") else f"@{username}"

    input_selectors = [
        "input[placeholder*='Instagram username']",
        "input[placeholder*='profile URL']",
        "input[type='text']",
    ]

    search = None
    for sel in input_selectors:
        try:
            search = WebDriverWait(driver, 12).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            if search:
                break
        except Exception:
            continue

    if not search:
        logger.warning("SocialCat fallback: input not found for @%s", username)
        return None

    try:
        search.click()
        search.send_keys(Keys.CONTROL, "a")
        search.send_keys(Keys.BACKSPACE)
        search.send_keys(query)
    except Exception:
        try:
            driver.execute_script(
                """
                const el = arguments[0];
                el.value = arguments[1];
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                search,
                query,
            )
        except Exception:
            pass

    # Click "Check Engagement Rate" if available; otherwise submit Enter.
    clicked = False
    for xpath in (
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'check engagement rate')]",
        "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'already showing this profile')]",
    ):
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            btn.click()
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        try:
            search.send_keys(Keys.RETURN)
        except Exception:
            pass

    er = _extract_socialcat_er(driver)
    if er:
        logger.info("SocialCat ER for @%s: %s", username, er)
    else:
        logger.warning("SocialCat fallback returned no ER for @%s", username)
    return er


# ── Extract ER ───────────────────────────────────────────────────────────────


def _extract_er(driver) -> Optional[str]:
    try_again_clicks = 0

    for i in range(ER_MAX_POLLS):
        time.sleep(ER_POLL_INTERVAL_SECONDS)
        try:
            text = driver.find_element(
                By.CSS_SELECTOR, "#er-calculator"
            ).text
            if i == 0:
                logger.info("ER calculator text: %s", text[:500] if text else "(empty)")
        except Exception:
            text = ""

        # If error state appears, click "Try again" immediately.
        if text and "something went wrong" in text.lower():
            logger.info("TrendHero error shown — clicking Try again")
            if _click_try_again(driver):
                try_again_clicks += 1
                # After clicking, allow UI to reset then continue polling.
                time.sleep(3)
                # Re-submit check after repeated server-side failures.
                if try_again_clicks >= 2:
                    _click_check(driver)
                continue

            # If button wasn't clickable yet, continue polling and retry click.
            continue

        # Strategy 1: JS regex — "X.XX% ... Engagement Rate"
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

        # Strategy 2: broader — any "X.XX%" in the calculator area
        if text:
            matches = re.findall(r"(\d+\.?\d*)\s*%", text)
            if matches:
                return matches[0] + "%"

        # Strategy 3: look for a dedicated result element
        for sel in (
            "[class*='engagement'] [class*='rate']",
            "[class*='result'] [class*='rate']",
            "[class*='er-result']",
            ".MuiTypography-root",
        ):
            try:
                els = driver.find_elements(By.CSS_SELECTOR, f"#er-calculator {sel}")
                for el in els:
                    m = re.search(r"(\d+\.?\d*)\s*%", el.text)
                    if m:
                        return m.group(1) + "%"
            except Exception:
                pass
    return None


# ── Public API ───────────────────────────────────────────────────────────────


def get_engagement_rate(
    ig_username: str,
    chrome_version: int = 0,
    headless: bool = True,
) -> Optional[str]:
    """
    Launch an undetected Chrome instance, navigate to TrendHero,
    solve CAPTCHA, and return the engagement rate string (e.g. ``"4.23%"``).

    Runs **headless** by default so the browser stays in the background.
    If *chrome_version* is ``0``, the installed Chrome version
    is detected automatically from the system.
    """
    logger.info("Fetching ER for @%s (headless=%s)", ig_username, headless)

    ver = chrome_version or _detect_chrome_major()
    driver = None

    try:
        for attempt in range(1, ER_MAX_USER_ATTEMPTS + 1):
            try:
                if driver is None:
                    driver = _make_uc_driver(ver, headless=headless)

                er = _attempt_fetch_er(driver, ig_username)
                if not er:
                    er = _attempt_fetch_er_socialcat(driver, ig_username)
                if er:
                    logger.info("ER for @%s: %s", ig_username, er)
                    return er

                logger.warning(
                    "No ER for @%s on attempt %d/%d",
                    ig_username,
                    attempt,
                    ER_MAX_USER_ATTEMPTS,
                )
            except Exception as exc:
                logger.warning(
                    "Attempt %d/%d failed for @%s: %s",
                    attempt,
                    ER_MAX_USER_ATTEMPTS,
                    ig_username,
                    exc,
                )

            _safe_quit(driver)
            driver = None
            time.sleep(ER_RETRY_BACKOFF_SECONDS)

        return None
    finally:
        _safe_quit(driver)


def _make_uc_driver(ver: int, headless: bool = False) -> uc.Chrome:
    """Create a new undetected-chromedriver instance.

    In server/container environments (no display), forces headless mode.
    If headed startup fails, retries once in headless mode.
    """
    force_headless = headless
    if platform.system() == "Linux" and not os.getenv("DISPLAY"):
        force_headless = True

    opts = uc.ChromeOptions()
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-crash-reporter")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--mute-audio")
    opts.add_argument("--disable-features=IsolateOrigins,site-per-process")
    opts.add_argument("--disable-site-isolation-trials")

    if force_headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-software-rasterizer")

    kw: dict = {"options": opts}
    if ver:
        kw["version_main"] = ver

    try:
        driver = uc.Chrome(**kw)
    except Exception as exc:
        if not force_headless:
            logger.warning(
                "Headed Chrome startup failed (%s); retrying headless for engagement",
                exc,
            )
            opts.add_argument("--headless=new")
            opts.add_argument("--disable-software-rasterizer")
            driver = uc.Chrome(**kw)
            force_headless = True
        else:
            raise

    if not force_headless:
        try:
            driver.minimize_window()
        except Exception:
            pass

    return driver


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
    headless: bool = True,
) -> dict[str, Optional[str]]:
    """
    Fetch engagement rates for multiple IG usernames.

    Runs **headless** by default so the browser stays in the background.
    Uses a single browser session where possible, but **automatically
    restarts Chrome** if the window crashes (up to *max_restarts* times
    per username). If *chrome_version* is ``0``, the installed Chrome
    version is detected automatically from the system.
    """
    results: dict[str, Optional[str]] = {}
    ver = chrome_version or _detect_chrome_major()
    driver = _make_uc_driver(ver, headless=headless)

    for username in ig_usernames:
        logger.info("Fetching ER for @%s", username)
        success = False

        attempts_for_user = max(max_restarts, ER_MAX_USER_ATTEMPTS)
        for attempt in range(1, attempts_for_user + 1):
            try:
                if driver is None:
                    driver = _make_uc_driver(ver, headless=headless)

                er = _attempt_fetch_er(driver, username)
                if not er:
                    er = _attempt_fetch_er_socialcat(driver, username)
                results[username] = er
                if er:
                    logger.info("ER for @%s: %s", username, er)
                    success = True
                    break
                else:
                    logger.warning(
                        "No ER for @%s on attempt %d/%d",
                        username,
                        attempt,
                        attempts_for_user,
                    )

            except Exception as e:
                logger.warning(
                    "Attempt %d/%d for @%s failed: %s",
                    attempt, attempts_for_user, username, e,
                )

            # Restart browser between failed attempts to avoid stale anti-bot state.
            _safe_quit(driver)
            if attempt < attempts_for_user:
                logger.info("Restarting Chrome for retry…")
                time.sleep(ER_RETRY_BACKOFF_SECONDS)
                try:
                    driver = _make_uc_driver(ver, headless=headless)
                except Exception as restart_err:
                    logger.error("Chrome restart failed: %s", restart_err)
                    results[username] = None
                    success = True
                    break

        if not success:
            results[username] = None

        # Prepare clean session for the next artist if last loop broke on success.
        _safe_quit(driver)
        try:
            driver = _make_uc_driver(ver, headless=headless)
        except Exception:
            driver = None

        time.sleep(2)

    _safe_quit(driver)
    return results
