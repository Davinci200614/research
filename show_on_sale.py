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
LOGIN_URL = "https://app.showsonsale.com/login"
SEARCH_URL = "https://app.showsonsale.com/app/search?page=all&search="
email = os.getenv("Mail_Address1")
password = os.getenv("Mail_Password1")
SHOWSONSALE_CSV = os.path.join(os.path.dirname(__file__), "Artist_Research - showsonsale.csv")
HOME_URL = "https://app.showsonsale.com/"


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
        EC.presence_of_element_located((By.NAME, "userName"))
    )
    email_input.clear()
    email_input.send_keys(email)
    time.sleep(0.5)

    password_input = driver.find_element(By.NAME, "password")
    password_input.clear()
    password_input.send_keys(password)
    time.sleep(0.5)

    # Click "login" button
    sign_in_btn = None
    sign_in_btn = driver.find_element(By.XPATH, "//*[@id='app']/main/div[2]/div[1]/div/form/div[5]/button")


    if sign_in_btn:
        sign_in_btn.click()
        print("   Clicked 'Login' button.")
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


# ── Presale event scraping ──


def _debug_dom(driver):
    """Dump page structure info to help diagnose selector issues."""
    selectors = [
        "table", "table thead", "table tbody", "table tbody tr", "table tr",
        "th", "td", "tr",
        "[role='table']", "[role='row']", "[role='rowgroup']",
        ".event-row", ".event", ".row",
    ]
    print("\n   [DEBUG] DOM element counts:")
    for sel in selectors:
        try:
            count = len(driver.find_elements(By.CSS_SELECTOR, sel))
            if count:
                print(f"      {sel}: {count}")
        except Exception:
            pass

    # Also dump an HTML snippet around the events area
    snippet = driver.execute_script("""
        // Try to find the events container
        const body = document.body.innerHTML;
        // Find position of 'Event Name' header text
        const idx = body.indexOf('Event Name');
        if (idx === -1) return 'Event Name not found in innerHTML';
        return body.substring(Math.max(0, idx - 500), idx + 2000);
    """)
    # Save full snippet to file for inspection
    snippet_path = os.path.join(os.path.dirname(__file__), "_debug_dom.html")
    with open(snippet_path, "w", encoding="utf-8") as f:
        f.write(snippet)
    print(f"      Saved HTML snippet to {snippet_path}")


def find_column_indices(header_cells):
    """Detect column positions from a list of header cell texts.

    Handles the combo column "Event Date - Presale - Public" separately
    so it doesn't overwrite the real "Event Date" column.
    """
    col_indices = {}
    for i, text in enumerate(header_cells):
        t = text.strip().lower().replace("\n", " ")

        # Skip the combo column "event date - presale - public"
        if "presale" in t and "public" in t:
            col_indices["Event Date-Presale-Public"] = i
            continue

        if "event name" in t or t == "event":
            col_indices["Event Name"] = i
        elif "price" in t and "event" not in t:
            col_indices["Price"] = i
        elif "venue" in t and "event" not in t:
            col_indices["Venue"] = i
        elif "city" in t and ("state" in t or "country" in t):
            col_indices["City,State,Country"] = i
        elif t == "location":
            col_indices["Location"] = i
        elif "event date" in t:
            col_indices["Event Date"] = i
        elif "presale begin" in t or "presale start" in t:
            col_indices["Presale Begin Date Time"] = i
        elif ("public" in t and "sale" in t) or "onsale" in t:
            col_indices["Puclic Sale Date Time"] = i
        elif t.startswith("presale") and "begin" not in t and "start" not in t:
            col_indices["Presale"] = i
        elif "category" in t:
            col_indices["Category"] = i

    return col_indices


def _compute_col_offset(driver, header_count, cells_sel):
    """Detect the offset between header cells and data-row cells.

    Headers may include extra non-data cells (checkboxes, icons) that
    don't appear as [role='cell'] in data rows.
    """
    data_rows = driver.find_elements(By.CSS_SELECTOR, "[role='row']")
    for row in data_rows:
        txt = row.text.strip()
        if not txt or "Presales -" in txt:
            continue
        cells = row.find_elements(By.CSS_SELECTOR, cells_sel)
        if len(cells) >= 4:
            offset = header_count - len(cells)
            if offset < 0:
                offset = 0
            return offset, len(cells)
    return 0, header_count


def _find_scroll_container(driver):
    """Find the scrollable container for the events table.

    Tries multiple strategies:
    1. Walk up from [role='row'] to find overflow:auto/scroll parent
    2. Find any div whose scrollHeight greatly exceeds clientHeight
    3. Try the parent of [role='rowgroup'] elements
    """
    container = driver.execute_script("""
        // Strategy 1: Walk up from a data row
        const rows = document.querySelectorAll("[role='row']");
        for (const row of rows) {
            let el = row.parentElement;
            while (el && el !== document.body && el !== document.documentElement) {
                const style = window.getComputedStyle(el);
                const oy = style.overflowY;
                const ox = style.overflow;
                if ((oy === 'auto' || oy === 'scroll' || ox === 'auto' || ox === 'scroll')
                    && el.scrollHeight > el.clientHeight + 50) {
                    return el;
                }
                el = el.parentElement;
            }
        }

        // Strategy 2: Find any div with large scrollHeight that contains role='row'
        const divs = document.querySelectorAll('div');
        let best = null;
        let bestDiff = 0;
        for (const div of divs) {
            if (div.scrollHeight > div.clientHeight + 100 && div.querySelector("[role='row']")) {
                const diff = div.scrollHeight - div.clientHeight;
                if (diff > bestDiff) {
                    bestDiff = diff;
                    best = div;
                }
            }
        }
        if (best) return best;

        // Strategy 3: parent of rowgroup
        const rg = document.querySelector("[role='rowgroup']");
        if (rg) {
            let el = rg.parentElement;
            while (el && el !== document.body) {
                if (el.scrollHeight > el.clientHeight + 50) return el;
                el = el.parentElement;
            }
        }

        return null;
    """)
    return container


def _find_rows_and_headers(driver):
    """Try multiple selector strategies to find table rows and header texts."""

    strategies = [
        # Strategy 1: standard <table>
        {
            "headers": "table thead th",
            "rows": "table tbody tr",
            "cells": "td",
        },
        # Strategy 2: <table> without explicit <thead>/<tbody>
        {
            "headers": "table tr:first-child th, table tr:first-child td",
            "rows": "table tr",
            "cells": "td",
        },
        # Strategy 3: plain <tr> anywhere
        {
            "headers": "th",
            "rows": "tr",
            "cells": "td",
        },
        # Strategy 4: ARIA roles
        {
            "headers": "[role='columnheader']",
            "rows": "[role='row']",
            "cells": "[role='cell'], [role='gridcell']",
        },
        # Strategy 5: div‑based grid with common class patterns
        {
            "headers": None,
            "rows": "div.rt-tr, div.rdt_TableRow, div[class*='row'], div[class*='Row']",
            "cells": "div.rt-td, div.rdt_TableCell, div[class*='cell'], div[class*='Cell']",
        },
    ]

    for idx, strat in enumerate(strategies, 1):
        rows_sel = strat["rows"]
        cells_sel = strat["cells"]

        rows = driver.find_elements(By.CSS_SELECTOR, rows_sel)
        if len(rows) < 2:
            continue

        # Get header texts
        header_texts = []
        if strat.get("headers"):
            header_els = driver.find_elements(By.CSS_SELECTOR, strat["headers"])
            header_texts = [h.text.strip() for h in header_els]

        # If no explicit headers, try first row's cells
        if not header_texts:
            first_cells = rows[0].find_elements(By.CSS_SELECTOR, cells_sel)
            if not first_cells:
                first_cells = rows[0].find_elements(By.XPATH, "./*")
            header_texts = [c.text.strip() for c in first_cells]

        print(f"   Strategy {idx}: {len(rows)} rows, headers={header_texts[:8]}")

        # Validate: at least one header should mention 'event' or 'venue'
        joined = " ".join(header_texts).lower()
        if "event" in joined or "venue" in joined or "price" in joined:
            return rows, header_texts, cells_sel

    return [], [], "td"


def scrape_presale_events(driver):
    """Scrape all presale events from the ShowsOnSale events table.

    The table uses virtualized rendering — only rows visible in the viewport
    exist in the DOM. We scroll the table container incrementally and collect
    rows as they appear, deduplicating by (event_name, venue, event_date).
    """
    print("\n" + "=" * 50)
    print("  Scraping presale events")
    print("=" * 50)

    # Wait for the page to have event content
    for _ in range(20):
        body = driver.find_element(By.TAG_NAME, "body").text
        if "Found" in body and "Events" in body:
            break
        time.sleep(1)
    time.sleep(3)

    # Print total event count
    expected_count = 0
    try:
        found_el = driver.find_element(
            By.XPATH, "//*[contains(text(),'Found') and contains(text(),'Events')]"
        )
        found_text = found_el.text.strip()
        print(f"   {found_text}")
        m = re.search(r"Found\s+(\d+)\s+Events", found_text)
        if m:
            expected_count = int(m.group(1))
    except Exception:
        pass

    # Expand all collapsed groups
    try:
        expand = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.LINK_TEXT, "Expand All"))
        )
        expand.click()
        print("   Expanded all event groups")
        time.sleep(5)
    except Exception:
        try:
            expand = driver.find_element(
                By.XPATH, "//*[contains(text(),'Expand All')]"
            )
            expand.click()
            print("   Expanded all event groups (fallback)")
            time.sleep(5)
        except Exception:
            print("   Warning: Could not click 'Expand All'")

    # Debug: dump element counts
    _debug_dom(driver)

    # ── Detect column indices from header ──
    cells_sel = "[role='cell'], [role='gridcell']"
    header_els = driver.find_elements(By.CSS_SELECTOR, "[role='columnheader']")
    header_texts = [h.text.strip() for h in header_els]
    if not header_texts:
        header_els = driver.find_elements(By.CSS_SELECTOR, "th")
        header_texts = [h.text.strip() for h in header_els]
        cells_sel = "td"
    print(f"   All header texts: {header_texts}")

    col_indices = find_column_indices(header_texts)
    if not col_indices:
        print("   Header detection failed – using positional defaults")
        col_indices = {
            "Event Name": 2, "Price": 3, "Venue": 4,
            "City,State,Country": 5, "Location": 6,
            "Event Date": 7, "Presale Begin Date Time": 8,
        }

    # ── Detect offset between header cells and data cells ──
    # Headers include extra non-data cells (checkboxes/icons) that don't
    # appear as [role='cell'] in data rows.
    offset, data_cell_count = _compute_col_offset(driver, len(header_texts), cells_sel)
    print(f"   Header cells: {len(header_texts)}, Data cells per row: {data_cell_count}, Offset: {offset}")

    # Adjust all column indices by subtracting the offset
    if offset > 0:
        adjusted = {}
        for col_name, idx in col_indices.items():
            new_idx = idx - offset
            if new_idx >= 0:
                adjusted[col_name] = new_idx
        col_indices = adjusted

    print(f"   Adjusted columns: {col_indices}")

    # ── Debug: dump first data row cell texts ──
    data_rows = driver.find_elements(By.CSS_SELECTOR, "[role='row']")
    for row in data_rows:
        txt = row.text.strip()
        if not txt or "Presales -" in txt:
            continue
        cells = row.find_elements(By.CSS_SELECTOR, cells_sel)
        if len(cells) >= 4:
            cell_texts = [c.text.strip()[:40] for c in cells]
            print(f"   Sample data row ({len(cells)} cells): {cell_texts}")
            break

    # ── Known header values to skip ──
    header_values = {v.lower() for v in header_texts if v}

    # ── Find the scrollable container ──
    scroll_container = _find_scroll_container(driver)
    if scroll_container:
        print("   Found scrollable table container")
    else:
        print("   No scrollable container found")

    # ── Helper to extract events from currently visible rows ──
    def _collect_visible(events, seen, current_presale_dt):
        visible_rows = driver.find_elements(By.CSS_SELECTOR, "[role='row']")
        if not visible_rows:
            visible_rows = driver.find_elements(By.CSS_SELECTOR, "tr")

        new_count = 0
        for row in visible_rows:
            try:
                row_text = row.text.strip()
                if not row_text:
                    continue

                if "Presales -" in row_text:
                    match = re.search(
                        r"Presales\s*-\s*(.+?)\s*\((?:GMT|UTC)", row_text
                    )
                    if match:
                        current_presale_dt = match.group(1).strip()
                    continue

                cells = row.find_elements(By.CSS_SELECTOR, cells_sel)
                if not cells:
                    cells = row.find_elements(By.XPATH, "./*")
                if len(cells) < 4:
                    continue

                event_name = ""
                if "Event Name" in col_indices:
                    idx = col_indices["Event Name"]
                    if idx < len(cells):
                        cell = cells[idx]
                        links = cell.find_elements(By.TAG_NAME, "a")
                        event_name = (
                            links[0].text.strip() if links
                            else cell.text.strip().split("\n")[0]
                        )

                if not event_name:
                    continue
                if event_name.lower() in header_values:
                    continue
                if event_name.lower() in ("event name", "event", "venue", "price"):
                    continue

                event = {
                    "Event Name": event_name,
                    "Price": "",
                    "Venue": "",
                    "City,State,Country": "",
                    "Location": "",
                    "Event Date": "",
                    "Presale": "",
                    "Presale Begin Date Time": "",
                    "Puclic Sale Date Time": "",
                    "Event Date-Presale-Public": "",
                    "Category": "",
                }

                for col_name, idx in col_indices.items():
                    if col_name == "Event Name":
                        continue
                    if idx >= len(cells):
                        continue
                    cell = cells[idx]
                    if col_name == "Location":
                        event[col_name] = cell.text.strip().replace("\n", ", ")
                    else:
                        event[col_name] = cell.text.strip().replace("\n", " ")

                if not event["Presale Begin Date Time"] and current_presale_dt:
                    event["Presale Begin Date Time"] = current_presale_dt

                key = (event["Event Name"], event.get("Venue", ""), event.get("Event Date", ""))
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)
                new_count += 1
            except Exception:
                continue

        return new_count, current_presale_dt

    # ── Try multiple scroll strategies to collect all events ──
    events = []
    seen = set()
    current_presale_dt = ""
    max_scrolls = 5000
    scroll_step = 400

    # --- Strategy A: scroll the detected container ---
    if scroll_container:
        print("   Strategy A: scrolling detected container...")
        driver.execute_script("arguments[0].scrollTop = 0;", scroll_container)
        time.sleep(1)
        stale = 0
        for i in range(max_scrolls):
            n, current_presale_dt = _collect_visible(events, seen, current_presale_dt)
            if n == 0:
                stale += 1
                if stale >= 20:
                    break
            else:
                stale = 0
            if expected_count and len(events) >= expected_count:
                break
            if i > 0 and i % 50 == 0:
                print(f"      ... {len(events)} events (scroll {i})")
            driver.execute_script(f"arguments[0].scrollTop += {scroll_step};", scroll_container)
            time.sleep(0.3)

    # --- Strategy B: scroll window ---
    if len(events) < (expected_count or 50):
        print(f"   Strategy B: scrolling window... ({len(events)} so far)")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)
        stale = 0
        for i in range(max_scrolls):
            n, current_presale_dt = _collect_visible(events, seen, current_presale_dt)
            if n == 0:
                stale += 1
                if stale >= 20:
                    break
            else:
                stale = 0
            if expected_count and len(events) >= expected_count:
                break
            if i > 0 and i % 50 == 0:
                print(f"      ... {len(events)} events (scroll {i})")
            driver.execute_script(f"window.scrollBy(0, {scroll_step});")
            time.sleep(0.3)

    # --- Strategy C: scroll every ancestor of the first row ---
    if len(events) < (expected_count or 50):
        print(f"   Strategy C: trying all scrollable ancestors... ({len(events)} so far)")
        ancestors = driver.execute_script("""
            const row = document.querySelector("[role='row']");
            if (!row) return [];
            const result = [];
            let el = row.parentElement;
            while (el && el !== document.documentElement) {
                if (el.scrollHeight > el.clientHeight + 20) {
                    result.push(el);
                }
                el = el.parentElement;
            }
            return result;
        """) or []
        print(f"      Found {len(ancestors)} scrollable ancestor(s)")
        for ai, ancestor in enumerate(ancestors):
            tag = driver.execute_script(
                "return arguments[0].tagName + '.' + arguments[0].className.split(' ')[0];",
                ancestor
            )
            sh = driver.execute_script("return arguments[0].scrollHeight;", ancestor)
            ch = driver.execute_script("return arguments[0].clientHeight;", ancestor)
            print(f"      Ancestor {ai}: {tag} scrollH={sh} clientH={ch}")

            prev_count = len(events)
            driver.execute_script("arguments[0].scrollTop = 0;", ancestor)
            time.sleep(0.5)
            stale = 0
            for j in range(max_scrolls):
                n, current_presale_dt = _collect_visible(events, seen, current_presale_dt)
                if n == 0:
                    stale += 1
                    if stale >= 20:
                        break
                else:
                    stale = 0
                if expected_count and len(events) >= expected_count:
                    break
                if j > 0 and j % 50 == 0:
                    print(f"      ... {len(events)} events (ancestor {ai}, scroll {j})")
                driver.execute_script(f"arguments[0].scrollTop += {scroll_step};", ancestor)
                time.sleep(0.3)

            new_from_this = len(events) - prev_count
            print(f"      Ancestor {ai} yielded {new_from_this} new events")
            if expected_count and len(events) >= expected_count:
                break

    # --- Strategy D: Use Page Down key on data rows ---
    if len(events) < (expected_count or 50):
        print(f"   Strategy D: keyboard scrolling... ({len(events)} so far)")
        try:
            # Click on the first data row to focus the table area
            first_data_row = None
            for row in driver.find_elements(By.CSS_SELECTOR, "[role='row']"):
                txt = row.text.strip()
                if txt and "Presales -" not in txt:
                    cells = row.find_elements(By.CSS_SELECTOR, cells_sel)
                    if len(cells) >= 4:
                        first_data_row = row
                        break
            if first_data_row:
                driver.execute_script("arguments[0].scrollIntoView(true);", first_data_row)
                time.sleep(0.5)
                first_data_row.click()
                time.sleep(0.5)

            body = driver.find_element(By.TAG_NAME, "body")
            stale = 0
            for i in range(max_scrolls):
                n, current_presale_dt = _collect_visible(events, seen, current_presale_dt)
                if n == 0:
                    stale += 1
                    if stale >= 30:
                        break
                else:
                    stale = 0
                if expected_count and len(events) >= expected_count:
                    break
                if i > 0 and i % 50 == 0:
                    print(f"      ... {len(events)} events (key scroll {i})")
                body.send_keys(Keys.PAGE_DOWN)
                time.sleep(0.4)
        except Exception as e:
            print(f"      Keyboard scroll failed: {e}")

    print(f"   Extracted {len(events)} events")
    return events


def _parse_events_from_text(driver):
    """Last-resort: parse the full page text line-by-line to extract events."""
    print("   Attempting text-based extraction...")
    page_text = driver.find_element(By.TAG_NAME, "body").text
    lines = page_text.split("\n")

    # Save full page text for debugging
    debug_path = os.path.join(os.path.dirname(__file__), "_debug_page_text.txt")
    with open(debug_path, "w", encoding="utf-8") as f:
        f.write(page_text)
    print(f"   Saved full page text to {os.path.basename(debug_path)} ({len(lines)} lines)")

    events = []
    current_presale_dt = ""

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Detect group headers: "Presales - Fri, Feb 13, 2026 12:00 AM (GMT+1)"
        presale_match = re.search(
            r"Presales\s*-\s*(.+?)\s*\((?:GMT|UTC)", line
        )
        if presale_match:
            current_presale_dt = presale_match.group(1).strip()
            i += 1
            continue

        # Look for event name lines — they usually contain bracket tags like [LIVN], [TMAU], [TWEB]
        if re.search(r'\[(?:LIVN|TMAU|TWEB|STUB|AXSS|DICE|ETIX|TCOM|EVNT)\]', line):
            event = {
                "Event Name": line,
                "Price": "",
                "Venue": "",
                "City,State,Country": "",
                "Location": "",
                "Event Date": "",
                "Presale": "",
                "Presale Begin Date Time": current_presale_dt,
                "Puclic Sale Date Time": "",
                "Event Date-Presale-Public": "",
                "Category": "",
            }

            # Look ahead for venue, location, date in the next few lines
            for j in range(1, min(8, len(lines) - i)):
                next_line = lines[i + j].strip()
                if not next_line:
                    continue
                # Price: starts with $
                if re.match(r'^\$\d', next_line) and not event["Price"]:
                    event["Price"] = next_line
                # Date: e.g. "Thu, Oct 29, 2026 12:00 AM"
                elif re.match(r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)', next_line) and not event["Event Date"]:
                    event["Event Date"] = next_line
                # City, State, Country
                elif re.search(r'(?:United States|Canada|Australia|United Kingdom)', next_line) and not event["City,State,Country"]:
                    event["City,State,Country"] = next_line

            events.append(event)

        i += 1

    print(f"   Text-based extraction: {len(events)} events")
    return events


def write_showsonsale_csv(events):
    """Write scraped presale events to the ShowsOnSale CSV file."""
    csv_headers = [
        "Event Name", "Price", "Venue", "City,State,Country", "Location",
        "Event Date", "Presale", "Presale Begin Date Time",
        "Puclic Sale Date Time", "Event Date-Presale-Public", "Category",
    ]
    with open(SHOWSONSALE_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(csv_headers)
        for event in events:
            w.writerow([event.get(h, "") for h in csv_headers])

    print(f"\n   \u2713 Wrote {len(events)} events to {os.path.basename(SHOWSONSALE_CSV)}")


def scrape_presales_main():
    """Login to ShowsOnSale and scrape the presale events table."""
    print("\nShowsOnSale Presale Event Scraper\n")

    if not email or not password:
        print("ERROR: Mail_Address1 or Mail_Password1 not set in .env file.")
        return

    chrome_options = Options()
    driver = webdriver.Chrome(options=chrome_options)

    try:
        if not login(driver):
            print("Login failed. Exiting.")
            return

        # After login the page may redirect to "too-many-connections".
        # Click "Terminate This Connection" to free the session, then re-login.
        time.sleep(3)
        for attempt in range(3):
            if "too-many-connections" in driver.current_url:
                print(f"   Too-many-connections detected (attempt {attempt+1}/3)")
                try:
                    terminate_btn = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            "//button[contains(text(),'Terminate This Connection')] | "
                            "//a[contains(text(),'Terminate This Connection')] | "
                            "//input[@value='Terminate This Connection']"
                        ))
                    )
                    terminate_btn.click()
                    print("   Clicked 'Terminate This Connection'")
                    time.sleep(5)

                    # After terminating, we may need to log in again
                    if "/login" in driver.current_url or "too-many" in driver.current_url:
                        driver.get(LOGIN_URL)
                        time.sleep(3)
                        login(driver)
                        time.sleep(3)
                except Exception as e:
                    print(f"   Could not find terminate button: {e}")
                    time.sleep(5)
                    driver.get(HOME_URL)
                    time.sleep(5)
            else:
                break

        # If not on dashboard, navigate there
        if "dashboard" not in driver.current_url and "/app" not in driver.current_url:
            driver.get(HOME_URL)
            time.sleep(5)

        print(f"   Current URL: {driver.current_url}")

        events = scrape_presale_events(driver)
        if events:
            write_showsonsale_csv(events)
        else:
            print("   No events found.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        try:
            driver.quit()
        except OSError:
            pass

    print("\nDone!")


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
    print("ShowsOnSale Tools\n")
    print("  1) Scrape artist data (skip rows with existing data)")
    print("  2) Scrape artist data (overwrite all rows)")
    print("  3) Scrape presale events to CSV\n")
    choice = input("Choose mode [1/2/3]: ").strip()

    if choice == "3":
        scrape_presales_main()
        return

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
    driver = webdriver.Chrome(options=options)

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
