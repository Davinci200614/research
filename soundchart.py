import csv
import os
import re
import time
import urllib.parse

from dotenv import load_dotenv
from httpx import options
import undetected_chromedriver as uc
from openai import OpenAI
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from selenium import webdriver
from selenium.webdriver.chrome.options import Options



# Import engagement rate functions from scrape_engagement.py
from scrape_engagement import (
    solve_captcha,
    click_check,
    extract_er,
    URL as TRENDHERO_URL,
)

# ── Load .env file ──
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ── Config ──
CSV_FILE = os.path.join(os.path.dirname(__file__), "Artist_Research - Artist.csv")
LOGIN_URL = "https://app.soundcharts.com/login"
SEARCH_URL = "https://app.soundcharts.com/app/search?page=all&search="
email = os.getenv("Mail_Address")
password = os.getenv("Mail_Password")


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


# ── Login ──

def login(driver):
    """Log in to Soundcharts with email and password."""
    print("Navigating to Soundcharts login page...")
    driver.get(LOGIN_URL)
    time.sleep(3)

    # Wait for email input and fill credentials
    email_input = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.NAME, "email"))
    )
    email_input.clear()
    email_input.send_keys(email)
    time.sleep(0.5)

    password_input = driver.find_element(By.NAME, "password")
    password_input.clear()
    password_input.send_keys(password)
    time.sleep(0.5)

    # Click "Sign in with email" button
    sign_in_btn = None
    sign_in_btn = driver.find_element(By.XPATH, "//*[@id='root']/div[2]/div/div/div[2]/div[1]/form/button")


    if sign_in_btn:
        sign_in_btn.click()
        print("   Clicked 'Sign in' button.")
    else:
        # Fallback: press Enter
        password_input.send_keys(Keys.RETURN)
        print("   Pressed Enter to submit login form.")

    # Wait for redirect away from login page
    print("   Waiting for login to complete...")
    for _ in range(30):
        time.sleep(2)
        if "/login" not in driver.current_url:
            print(f"   ✓ Logged in! Current URL: {driver.current_url}")
            return True

    print("   ✗ Login may have failed — still on login page.")
    print(f"   Current URL: {driver.current_url}")
    return False


# ── Search for artist ──

def search_artist(driver, artist_name):
    """Search for an artist on Soundcharts and navigate to their profile."""
    print(f"\n{'='*50}")
    print(f"  Searching for: {artist_name}")
    print(f"{'='*50}")

    encoded_name = urllib.parse.quote(artist_name.upper())
    search_url = f"{SEARCH_URL}{encoded_name}"
    driver.get(search_url)
    time.sleep(5)


    # Click the first artist result — MUST be in the main content area, NOT the sidebar
    # The sidebar contains "Favorite artists" links that we need to avoid
    artist_link = None

        # Artist links inside main/section content (not sidebar nav)
    artist_link = driver.find_element(By.XPATH, "//*[@id='root']/div[2]/div/div[1]/div[3]/div/div/div[1]/div[2]/a[1]/div/div[1]/img")
       
    if artist_link:
        href = artist_link.get_attribute("title") or artist_link.get_attribute("alt") or artist_link.text
        print(f"   Found artist link: {href}")
        artist_link.click()
        time.sleep(5)

        # Wait for the overview page to load
        try:
            WebDriverWait(driver, 15).until(
                EC.url_contains("/app/artist/")
            )
            print(f"   ✓ On artist profile: {driver.current_url}")
            return True
        except Exception:
            print(f"   Current URL: {driver.current_url}")
            return "/app/artist/" in driver.current_url
    else:
        print(f"   ✗ No artist result found for '{artist_name}'")
        # Debug: print visible text
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text[:500]
            print(f"   [DEBUG] Page text: {body_text}")
        except Exception:
            pass
        return False


# ── Extract follower data from artist overview ──

def extract_follower_data(driver):
    """Extract platform follower counts from the artist overview page."""
    time.sleep(3)
    data = {}

    # Make sure we're on the Overview tab
    try:
        overview_tab = driver.find_element(By.XPATH, "//a[contains(text(),'Overview')] | //button[contains(text(),'Overview')]")
        if overview_tab:
            overview_tab.click()
            time.sleep(2)
    except Exception:
        pass  # Already on overview

    # Extract all visible text from the page for parsing
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        print("   Could not read page text.")
        return data

    # Platform patterns to look for on the overview page
    # Format on page: "PLATFORM FOLLOWERS\n<value>\n<change>"

    platform_patterns = {
        "tiktok_followers": r"TIKTOK FOLLOWERS\s*\n?\s*([\d.,]+[KMB]?)",
        "spotify_followers": r"SPOTIFY FOLLOWERS\s*\n?\s*([\d.,]+[KMB]?)",
        "instagram_followers": r"INSTAGRAM FOLLOWERS\s*\n?\s*([\d.,]+[KMB]?)",
        "bandsintown_followers": r"BANDSINTOWN FOLLOWERS\s*\n?\s*([\d.,]+[KMB]?)",
    }

    for key, pattern in platform_patterns.items():
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            data[key] = match.group(1)

    # Genre: try Genres first, fall back to Sub Genres
    genre_match = re.search(r"Genres\s*\n?\s*([^\n]+)", page_text, re.IGNORECASE)
    genre_val = genre_match.group(1).strip() if genre_match else ""
    if genre_val.lower().startswith("sub genre"):
        genre_val = ""  # matched the "Sub Genres" heading, not an actual genre
    if genre_val:
        data["genre"] = genre_val
    else:
        sub_genre_match = re.search(r"Sub Genres\s*\n?\s*([^\n]+)", page_text, re.IGNORECASE)
        if sub_genre_match and sub_genre_match.group(1).strip():
            data["genre"] = sub_genre_match.group(1).strip()

    if data:
        print("   Extracted follower data:")
        for k, v in data.items():
            print(f"      {k}: {v}")
    else:
        print("   No follower data extracted from page.")
        # Debug
        print(f"   [DEBUG] Page text (first 500 chars): {page_text[:500]}")

    return data


# ── Extract Instagram username from artist profile ──

def extract_ig_username(driver):
    """Extract Instagram username from the Sources section on the artist profile page."""
    time.sleep(2)

    ig_username = None
    strategies = [
        # Strategy 1: The exact XPath area (Sources section) — find any link with instagram.com
        lambda: driver.find_element(By.XPATH,
            "//*[@id='root']//a[contains(@href,'instagram.com')]"
        ),
        # Strategy 2: The specific Sources div area
        lambda: driver.find_element(By.XPATH,
            "//div[contains(@class,'source') or contains(@class,'Source')]//a[contains(@href,'instagram.com')]"
        ),
        # Strategy 3: Any anchor with instagram.com href on the page
        lambda: driver.find_element(By.CSS_SELECTOR,
            "a[href*='instagram.com']"
        ),
        # Strategy 4: JS fallback — find first instagram link not in nav/sidebar
        lambda: driver.execute_script("""
            const links = document.querySelectorAll('a[href*="instagram.com"]');
            for (const link of links) {
                const nav = link.closest('nav, aside');
                if (!nav) return link;
            }
            return links.length > 0 ? links[0] : null;
        """),
    ]

    for fn in strategies:
        try:
            el = fn()
            if el:
                href = el.get_attribute("href")
                if href and "instagram.com" in href:
                    # Extract username from URL like https://www.instagram.com/brunomars/ or similar
                    match = re.search(r'instagram\.com/([^/?&#]+)', href)
                    if match:
                        ig_username = match.group(1).strip('/')
                        # Skip generic pages
                        if ig_username.lower() not in ('', 'p', 'explore', 'accounts', 'about'):
                            print(f"   Found Instagram link: {href}")
                            print(f"   Extracted IG username: @{ig_username}")
                            return ig_username
        except Exception:
            pass

    print("   ✗ Could not find Instagram link on profile page.")
    return None


# ── Get engagement rate from TrendHERO using IG username ──

def get_engagement_rate(driver, ig_username):
    """Navigate to TrendHERO and get engagement rate for an IG username."""
    print(f"   Fetching engagement rate for @{ig_username}...")

    driver.get(TRENDHERO_URL)
    time.sleep(3)

    # Type IG username
    search = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#er-calculator input"))
    )
    search.clear()
    time.sleep(0.5)
    search.send_keys(ig_username)
    time.sleep(1)

    # Solve CAPTCHA
    if not solve_captcha(driver):
        print("   Skipping ER (CAPTCHA failed).")
        return None

    # Click Check
    driver.switch_to.default_content()
    time.sleep(1)
    click_check(driver)

    # Extract ER
    er = extract_er(driver)
    if er:
        print(f"   ✓ Engagement Rate: {er}")
    else:
        print("   ✗ Could not extract engagement rate.")
    return er


# ── Get tour link via OpenAI web search ──

def get_tour_link(artist_name):
    """Use OpenAI with web search to find the official tour/website link for an artist."""
    print(f"   Fetching tour link for {artist_name}...")
    try:
        ai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = (
            f'Search the web for the official tour page or official website of the artist "{artist_name}". '
            f'Return ONLY the URL. No text, no explanation, no markdown. Just the raw URL.'
        )
        response = ai_client.responses.create(
            model="gpt-4o",
            tools=[{"type": "web_search"}],
            input=prompt,
        )
        url = response.output_text.strip()
        # Auto-prepend https:// if response looks like a bare domain
        if url and not url.startswith("http://") and not url.startswith("https://"):
            if "." in url and " " not in url:
                url = "https://" + url
        if url.startswith("http://") or url.startswith("https://"):
            print(f"   \u2713 Tour link: {url}")
            return url
        print(f"   \u26a0 Unexpected response for {artist_name}: {url[:120]}")
        return url  # return anyway, user can review
    except Exception as e:
        print(f"   \u2717 Error fetching tour link for {artist_name}: {e}")
        return None


# ── Process a single artist ──

def process_artist(driver, artist_name):
    """Search for artist, go to profile, extract data + IG username."""
    if not search_artist(driver, artist_name):
        return None, None

    follower_data = extract_follower_data(driver)
    ig_username = extract_ig_username(driver)

    return follower_data, ig_username


# ── Update CSV row with extracted data ──

def update_row(row, headers, data, er=None):
    """Update a CSV row with extracted Soundcharts data and engagement rate."""
    col_mapping = {
        "Genre": "genre",
        "Ticktok Followers": "tiktok_followers",
        "Spotify Listeners": "spotify_followers",
        "Instagram followers": "instagram_followers",
        "Bandsintown Followors": "bandsintown_followers",
    }
    updated = False
    for csv_col, data_key in col_mapping.items():
        if csv_col in headers and data_key in data:
            idx = headers.index(csv_col)
            while len(row) <= idx:
                row.append("")
            row[idx] = data[data_key]
            updated = True

    # Update engagement rate column
    if er and "IG engagment rate" in headers:
        er_idx = headers.index("IG engagment rate")
        while len(row) <= er_idx:
            row.append("")
        row[er_idx] = er
        updated = True

    # Update tour link column
    tour_link = data.get("tour_link")
    if tour_link and "Link to Tour(artist website)" in headers:
        tour_idx = headers.index("Link to Tour(artist website)")
        while len(row) <= tour_idx:
            row.append("")
        row[tour_idx] = tour_link
        updated = True

    return updated


# ── Entry point ──

def main():
    print("Soundcharts Artist Data Scraper\n")
    print("  1) Skip rows that already have data (only empty)")
    print("  2) Process all rows (overwrite existing data)\n")
    choice = input("Choose mode [1/2]: ").strip()
    skip_existing = choice != "2"

    headers, rows = read_csv(CSV_FILE)
    artist_col = headers.index("Artist Name")

    # Columns to check for existing data
    data_cols = ["Genre", "Ticktok Followers", "Spotify Listeners", "Instagram followers", "Bandsintown Followors"]
    data_col_indices = [headers.index(c) for c in data_cols if c in headers]

    to_scrape = []
    for i, row in enumerate(rows):
        while len(row) < len(headers):
            row.append("")
        name = row[artist_col].strip()
        if not name:
            continue
        if skip_existing and any(row[idx].strip() for idx in data_col_indices):
            print(f"   Skipping {name} (already has data)")
            continue
        to_scrape.append((i, name))

    if not to_scrape:
        print("No artists to scrape. Done.")
        return

    mode_label = "empty rows only" if skip_existing else "all rows"
    print(f"\nMode: {mode_label}")
    print(f"Artists to scrape: {[name for _, name in to_scrape]}\n")
    if not email or not password:
        print("ERROR: Mail_Address or Mail_Password not set in .env file.")
        return
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    driver = webdriver.Chrome(options=options)
    driver.get("https://www.soundcharts.com/")
    print(driver.title)

    try:
        # Step 1: Login
        if not login(driver):
            print("Login failed. Exiting.")
            return

        time.sleep(3)

        # Step 2: Collect Soundcharts data + IG usernames for all artists
        collected = []  # (row_idx, name, follower_data, ig_username)
        failed = []

        for row_idx, name in to_scrape:
            follower_data, ig_username = process_artist(driver, name)
            if follower_data:
                collected.append((row_idx, name, follower_data, ig_username))
                # Save follower data immediately
                update_row(rows[row_idx], headers, follower_data)
                write_csv(CSV_FILE, headers, rows)
                print(f"   ✓ Follower data saved for {name}")
            else:
                print(f"   ✗ Skipped {name} — will retry")
                failed.append((row_idx, name))
            time.sleep(2)

        # Retry failed artists once
        if failed:
            print(f"\nRetrying {len(failed)} failed artist(s)...")
            for row_idx, name in failed:
                follower_data, ig_username = process_artist(driver, name)
                if follower_data:
                    collected.append((row_idx, name, follower_data, ig_username))
                    update_row(rows[row_idx], headers, follower_data)
                    write_csv(CSV_FILE, headers, rows)
                    print(f"   ✓ Follower data saved for {name} (retry)")
                else:
                    print(f"   ✗ Failed again: {name}")
                time.sleep(2)

        # Step 3: Get tour links via OpenAI web search (no browser needed)
        tour_col = "Link to Tour(artist website)"
        if tour_col in headers:
            tour_idx = headers.index(tour_col)
            tour_artists = [
                (ri, n) for ri, n, _, _ in collected
                if not rows[ri][tour_idx].strip()
            ]
            if tour_artists:
                print(f"\n{'='*50}")
                print(f"  Getting tour links for {len(tour_artists)} artist(s)...")
                print(f"{'='*50}")
                for row_idx, name in tour_artists:
                    link = get_tour_link(name)
                    if link:
                        update_row(rows[row_idx], headers, {"tour_link": link})
                        write_csv(CSV_FILE, headers, rows)
                        print(f"   \u2713 Tour link saved for {name}")
                    else:
                        print(f"   \u2717 No tour link found for {name}")
                    time.sleep(1)
            else:
                print("\n   All artists already have tour links — skipping.")
        else:
            print(f"\n   Column '{tour_col}' not found in CSV — skipping tour links.")

    except KeyboardInterrupt:
        print("\nInterrupted — saving progress...")
        write_csv(CSV_FILE, headers, rows)
    finally:
        try:
            driver.quit()
        except OSError:
            pass  # Suppress WinError 6 "handle is invalid" on cleanup

    # Step 4: Get engagement rates from TrendHERO using a separate
    #         undetected_chromedriver (visible) to handle CAPTCHA properly.
    #         Headless Chrome gets blocked by reCAPTCHA, so we need a real browser.
    er_artists = [(ri, n, ig) for ri, n, _, ig in collected if ig]
    if er_artists:
        # Check which artists still need ER
        headers, rows = read_csv(CSV_FILE)  # re-read in case tour links were saved
        er_col_name = "IG engagment rate"
        if er_col_name in headers:
            er_col_idx = headers.index(er_col_name)
            er_artists = [
                (ri, n, ig) for ri, n, ig in er_artists
                if not rows[ri][er_col_idx].strip()
            ]

        if er_artists:
            print(f"\n{'='*50}")
            print(f"  Getting engagement rates for {len(er_artists)} artist(s)...")
            print(f"  (Using undetected_chromedriver for CAPTCHA support)")
            print(f"{'='*50}")

            uc_options = uc.ChromeOptions()
            uc_options.add_argument("--start-maximized")
            er_driver = uc.Chrome(options=uc_options, version_main=144)

            try:
                for row_idx, name, ig_username in er_artists:
                    er = get_engagement_rate(er_driver, ig_username)
                    if er:
                        update_row(rows[row_idx], headers, {}, er=er)
                        write_csv(CSV_FILE, headers, rows)
                        print(f"   ✓ ER saved for {name} (@{ig_username}): {er}")
                    else:
                        print(f"   ✗ Could not get ER for {name} (@{ig_username})")
                    time.sleep(2)
            except KeyboardInterrupt:
                print("\nInterrupted — saving progress...")
                write_csv(CSV_FILE, headers, rows)
            finally:
                try:
                    er_driver.quit()
                except OSError:
                    pass
        else:
            print("\n   All artists already have engagement rates — skipping.")
    else:
        print("\n   No Instagram usernames found — skipping engagement rates.")

    print("\nDone! CSV updated.")


if __name__ == "__main__":
    main()
