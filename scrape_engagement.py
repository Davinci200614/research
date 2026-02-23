import csv
import os
import re
import subprocess
import tempfile
import time
import unicodedata

import imageio_ffmpeg
import requests
import speech_recognition as sr
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ── Config ──
CSV_FILE = os.path.join(os.path.dirname(__file__), "Artist_Research - Artist.csv")
URL = "https://trendhero.io/engagement-rate-calculator-instagram/"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
MAX_RETRIES = 3

# ── Instagram username mapping (display name → IG handle) ──
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


def get_ig_username(artist_name):
    """Convert artist display name to Instagram username."""
    key = artist_name.strip().lower()
    if key in IG_USERNAMES:
        return IG_USERNAMES[key]
    # Fallback: lowercase, strip accents, remove non-alphanumeric
    normalized = unicodedata.normalize("NFD", key)
    stripped = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", stripped)


# ── CSV helpers ──

def read_csv(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        return headers, list(reader)


def write_csv(path, headers, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


# ── CAPTCHA solver ──

def is_captcha_solved(driver):
    driver.switch_to.default_content()
    try:
        token = driver.execute_script(
            "return document.querySelector('textarea[name=\"g-recaptcha-response\"]')?.value || '';"
        )
        return bool(token)
    except Exception:
        return False


def switch_to_iframe(driver, src_keyword):
    """Switch to an iframe whose src contains the given keyword."""
    driver.switch_to.default_content()
    iframe = driver.find_element(By.CSS_SELECTOR, f"iframe[src*='{src_keyword}']")
    driver.switch_to.frame(iframe)


def transcribe_audio(audio_url):
    """Download captcha audio, convert to WAV, transcribe with Google."""
    tmp = tempfile.mkdtemp()
    mp3 = os.path.join(tmp, "audio.mp3")
    wav = os.path.join(tmp, "audio.wav")
    try:
        with open(mp3, "wb") as f:
            f.write(requests.get(audio_url, timeout=15).content)
        subprocess.run(
            [FFMPEG, "-y", "-i", mp3, "-ar", "16000", "-ac", "1", "-f", "wav", wav],
            capture_output=True, timeout=30
        )
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav) as src:
            return recognizer.recognize_google(recognizer.record(src))
    finally:
        for p in (mp3, wav):
            try: os.remove(p)
            except OSError: pass
        try: os.rmdir(tmp)
        except OSError: pass


def solve_captcha(driver):
    """Solve reCAPTCHA: click checkbox → audio challenge → manual fallback."""
    driver.switch_to.default_content()
    if not driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']"):
        print("   No CAPTCHA detected.")
        return True

    print("   CAPTCHA detected — solving...")

    # Scroll recaptcha iframe into view and click checkbox via JS to avoid interception
    try:
        driver.switch_to.default_content()
        iframe = driver.find_element(By.CSS_SELECTOR, "iframe[src*='recaptcha/api2/anchor']")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", iframe)
        time.sleep(1)
        # Use JS to click the iframe element itself to focus, then switch and click
        driver.switch_to.frame(iframe)
        checkbox = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "recaptcha-anchor"))
        )
        driver.execute_script("arguments[0].click();", checkbox)
        time.sleep(2)
    except Exception as e:
        print(f"   Could not click checkbox: {e}")
        driver.switch_to.default_content()
        return False

    if is_captcha_solved(driver):
        print("   CAPTCHA solved (checkbox only).")
        return True

    # Audio challenge loop
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"   Audio attempt {attempt}/{MAX_RETRIES}...")
        try:
            switch_to_iframe(driver, "recaptcha/api2/bframe")
            time.sleep(1)
            try:
                WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "recaptcha-audio-button"))
                ).click()
                time.sleep(3)
            except Exception:
                pass

            switch_to_iframe(driver, "recaptcha/api2/bframe")
            link = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".rc-audiochallenge-tdownload-link"))
            )
            audio_url = link.get_attribute("href")
            if not audio_url:
                audio_url = driver.find_element(By.CSS_SELECTOR, "audio source").get_attribute("src")

            text = transcribe_audio(audio_url)
            print(f"   Transcribed: '{text}'")

            switch_to_iframe(driver, "recaptcha/api2/bframe")
            driver.find_element(By.ID, "audio-response").send_keys(text)
            time.sleep(0.5)
            driver.find_element(By.ID, "recaptcha-verify-button").click()
            time.sleep(3)

            if is_captcha_solved(driver):
                print("   CAPTCHA solved (audio)!")
                driver.switch_to.default_content()
                return True

            switch_to_iframe(driver, "recaptcha/api2/bframe")
            driver.find_element(By.ID, "recaptcha-reload-button").click()
            time.sleep(2)
        except Exception as e:
            print(f"   Audio attempt failed: {e}")

    # Manual fallback
    print("   Auto-solve failed. Please solve manually (120s)...")
    driver.switch_to.default_content()
    for _ in range(60):
        if is_captcha_solved(driver):
            print("   CAPTCHA solved manually!")
            return True
        time.sleep(2)
    print("   CAPTCHA timeout.")
    return False


# ── Click Check button ──

def click_check(driver):
    """Click the Check button via multiple strategies."""
    driver.switch_to.default_content()
    time.sleep(1)
    strategies = [
        lambda: WebDriverWait(driver, 5).until(EC.element_to_be_clickable((
            By.CSS_SELECTOR,
            "#er-calculator > div > div.MuiCardContent-root-34.jss337 > form > div"
            " > div > div.MuiGrid-root-82.MuiGrid-item-84.MuiGrid-grid-xs-12-128"
            ".MuiGrid-grid-sm-auto-129 > span > button"
        ))).click(),
        lambda: driver.find_element(By.CSS_SELECTOR, "#er-calculator form button").click(),
        lambda: driver.find_element(By.XPATH, "//button[contains(text(),'Check')]").click(),
        lambda: driver.execute_script(
            "(document.querySelector('#er-calculator form button')"
            "||document.querySelector('#er-calculator button')).click();"
        ),
    ]
    for i, fn in enumerate(strategies, 1):
        try:
            fn()
            print(f"   Clicked Check (strategy {i}).")
            return
        except Exception:
            pass
    print("   Could not click Check button.")


# ── Extract engagement rate ──

def extract_er(driver):
    """Wait for results and extract engagement rate."""
    # Poll for up to 40 seconds for the ER to appear
    for wait_round in range(8):
        time.sleep(5)
        # JS extraction (matches "X.XX %\nEngagement Rate")
        try:
            val = driver.execute_script("""
                const el = document.querySelector('#er-calculator');
                if (!el) return null;
                const m = el.innerText.match(/(\\d+\\.?\\d*)\\s*%\\s*\\n?\\s*Engagement Rate/i);
                return m ? m[1] + '%' : null;
            """)
            if val:
                return val
        except Exception:
            pass

        # Fallback: regex on page text
        try:
            text = driver.find_element(By.CSS_SELECTOR, "#er-calculator").text
            matches = re.findall(r'(\d+\.?\d*)\s*%', text)
            if matches:
                return matches[0] + "%"
        except Exception:
            pass
    return None


# ── Main scrape function ──

def get_engagement_rate(driver, artist_name):
    ig_username = get_ig_username(artist_name)
    print(f"\n{'='*50}")
    print(f"  Processing: {artist_name} (IG: @{ig_username})")
    print(f"{'='*50}")

    driver.get(URL)
    time.sleep(3)

    # Type IG username (not display name)
    search = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#er-calculator input"))
    )
    search.clear()
    time.sleep(0.5)
    search.send_keys(ig_username)
    time.sleep(1)

    # Solve CAPTCHA
    if not solve_captcha(driver):
        print("   Skipping (CAPTCHA failed).")
        return None

    # Click Check
    driver.switch_to.default_content()
    time.sleep(1)
    click_check(driver)

    # Extract ER
    er = extract_er(driver)
    if not er:
        # Debug: print what the page shows
        try:
            text = driver.find_element(By.CSS_SELECTOR, "#er-calculator").text
            print(f"   [DEBUG] Page text (first 300 chars): {text[:300]}")
        except Exception:
            pass
    if er:
        print(f"   ✓ Engagement Rate: {er}")
    else:
        print("   ✗ Could not extract engagement rate.")
    return er


# ── Entry point ──

def main():
    print("TrendHERO IG Engagement Rate Scraper\n")
    print("  1) Skip rows that already have ER (only empty)")
    print("  2) Process all rows (overwrite existing ER)\n")
    choice = input("Choose mode [1/2]: ").strip()
    skip_existing = choice != "2"

    headers, rows = read_csv(CSV_FILE)
    artist_col = headers.index("Artist Name")
    er_col = headers.index("IG engagment rate")

    to_scrape = []
    for i, row in enumerate(rows):
        while len(row) < len(headers):
            row.append("")
        name = row[artist_col].strip()
        if not name:
            continue
        if skip_existing and row[er_col].strip():
            continue
        to_scrape.append((i, name))

    if not to_scrape:
        print("No artists to scrape. Done.")
        return

    mode_label = "empty rows only" if skip_existing else "all rows"
    print(f"\nMode: {mode_label}")
    print(f"Artists to scrape: {[name for _, name in to_scrape]}\n")

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    driver = uc.Chrome(options=options, version_main=144)

    try:
        failed = []
        for row_idx, name in to_scrape:
            if not name:
                continue
            er = get_engagement_rate(driver, name)
            if er:
                rows[row_idx][er_col] = er
                write_csv(CSV_FILE, headers, rows)
                print(f"   ✓ CSV updated for {name}")
            else:
                print(f"   ✗ Skipped {name} — will retry")
                failed.append((row_idx, name))
            time.sleep(2)

        # Retry failed artists once
        if failed:
            print(f"\nRetrying {len(failed)} failed artist(s)...")
            for row_idx, name in failed:
                er = get_engagement_rate(driver, name)
                if er:
                    rows[row_idx][er_col] = er
                    write_csv(CSV_FILE, headers, rows)
                    print(f"   ✓ CSV updated for {name} (retry)")
                else:
                    print(f"   ✗ Failed again: {name}")
                time.sleep(2)
    except KeyboardInterrupt:
        print("\nInterrupted — saving progress...")
        write_csv(CSV_FILE, headers, rows)
    finally:
        driver.quit()

    print("\nDone! CSV updated.")


if __name__ == "__main__":
    main()
