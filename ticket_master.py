"""
Ticketmaster Concert Scraper
─────────────────────────────
1. Fetches free proxies and finds a working one for Ticketmaster.
2. Reads artist names from the CSV.
3. Searches each artist on Ticketmaster via the search bar.
4. Clicks the first search suggestion.
5. Scrapes ALL concert info from the artist page.
6. Saves everything to a CSV file.
"""

import csv
import os
import random
import re
import time
import traceback

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ── Config ──
CSV_FILE = os.path.join(os.path.dirname(__file__), "Artist_Research - Artist.csv")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "Artist_Research - Ticketmaster_Concerts.csv")
PROXY_LIST_FILE = os.path.join(os.path.dirname(__file__), "proxy_list.txt")
TICKETMASTER_URL = "https://www.ticketmaster.com/"
SEARCH_INPUT_XPATH = '//*[@id="searchFormInput-input"]'


# ══════════════════════════════════════════════════════════════════════════════
#  PROXY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

FREE_PROXY_APIS = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&protocol=http&timeout=5000",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
]


def load_local_proxies():
    """Load proxies from the local proxy_list.txt file."""
    proxies = []
    if os.path.isfile(PROXY_LIST_FILE):
        with open(PROXY_LIST_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and re.match(r'^\d+\.\d+\.\d+\.\d+:\d+$', line):
                    # Skip obviously invalid entries
                    if not line.startswith("0.0.0.0") and not line.startswith("127.0.0"):
                        proxies.append(line)
        print(f"   Loaded {len(proxies)} proxies from proxy_list.txt")
    else:
        print("   proxy_list.txt not found.")
    return proxies


def fetch_fresh_proxies():
    """Fetch proxy lists from multiple free APIs and merge them."""
    all_proxies = set()
    for api_url in FREE_PROXY_APIS:
        try:
            resp = requests.get(api_url, timeout=10)
            if resp.status_code == 200:
                for line in resp.text.strip().splitlines():
                    line = line.strip()
                    line = re.sub(r'^https?://', '', line)
                    if re.match(r'^\d+\.\d+\.\d+\.\d+:\d+$', line):
                        if not line.startswith("0.0.0.0") and not line.startswith("127.0.0"):
                            all_proxies.add(line)
                print(f"   Fetched {len(all_proxies)} proxies so far from {api_url.split('/')[2]}")
        except Exception:
            pass
    print(f"   Total unique online proxies fetched: {len(all_proxies)}")
    return list(all_proxies)


def _test_proxies(proxies, max_tests):
    """Test a list of proxies against Ticketmaster, return the first working one."""
    test_url = "https://www.ticketmaster.com/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/144.0.0.0 Safari/537.36"
        ),
    }

    random.shuffle(proxies)
    print(f"   Testing up to {min(max_tests, len(proxies))} proxies...\n")

    for i, proxy_addr in enumerate(proxies[:max_tests]):
        proxy_url = f"http://{proxy_addr}"
        px = {"http": proxy_url, "https": proxy_url}
        try:
            r = requests.get(test_url, proxies=px, headers=headers, timeout=10)
            if r.status_code == 200 and len(r.text) > 5000:
                print(f"   [{i+1}] ✓ Working proxy found: {proxy_addr}")
                return proxy_addr
            else:
                print(f"   [{i+1}] ✗ {proxy_addr} — status {r.status_code}, len {len(r.text)}")
        except Exception as e:
            print(f"   [{i+1}] ✗ {proxy_addr} — {type(e).__name__}")
    return None


def find_working_proxy(max_tests=80):
    """
    Try local proxy_list.txt first, then fall back to fetching online.
    Returns the first proxy that gets a 200 from Ticketmaster, or None.
    """
    # ── Phase 1: Try proxies from proxy_list.txt ──
    print("\n🔍 Phase 1: Testing proxies from proxy_list.txt...")
    local_proxies = load_local_proxies()
    if local_proxies:
        result = _test_proxies(local_proxies, max_tests)
        if result:
            return result
        print("   ⚠ No working proxy found in proxy_list.txt.\n")

    # ── Phase 2: Fetch fresh proxies online ──
    print("🔍 Phase 2: Fetching fresh proxies online...")
    online_proxies = fetch_fresh_proxies()
    if online_proxies:
        result = _test_proxies(online_proxies, max_tests)
        if result:
            return result

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  CSV HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def read_csv(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        return headers, list(reader)


def write_concerts_csv(path, rows):
    """Write concert data to CSV.  Creates file with headers if needed."""
    headers = [
        "Artist Name", "Date", "Day", "Time", "City", "State",
        "Venue", "Tour / Event Name", "Presale Info", "Event URL",
    ]
    file_exists = os.path.isfile(path) and os.path.getsize(path) > 0
    with open(path, "a" if file_exists else "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(headers)
        w.writerows(rows)


# ══════════════════════════════════════════════════════════════════════════════
#  BROWSER SETUP
# ══════════════════════════════════════════════════════════════════════════════

def create_driver(proxy_addr=None):
    """Create an undetected Chrome driver, optionally with a proxy."""
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    if proxy_addr:
        options.add_argument(f"--proxy-server=http://{proxy_addr}")
        print(f"   Browser using proxy: {proxy_addr}")
    else:
        print("   Browser running WITHOUT proxy (direct connection)")

    driver = uc.Chrome(options=options, version_main=144)
    return driver


# ══════════════════════════════════════════════════════════════════════════════
#  SEARCH & CLICK FIRST SUGGESTION
# ══════════════════════════════════════════════════════════════════════════════

def search_artist_on_ticketmaster(driver, artist_name):
    """
    Type the artist name into the Ticketmaster search bar, click the
    Search button, then click the first top-suggestion link on the
    results page.  Returns True on success.
    """
    print(f"\n{'='*55}")
    print(f"   Searching Ticketmaster for: {artist_name}")
    print(f"{'='*55}")

    # Navigate to Ticketmaster home
    driver.get(TICKETMASTER_URL)
    time.sleep(4)

    # Find search input
    try:
        search_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, SEARCH_INPUT_XPATH))
        )
    except Exception:
        print("   ✗ Could not find search bar. Trying CSS fallback...")
        try:
            search_box = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input#searchFormInput-input"))
            )
        except Exception:
            print("   ✗ Search bar not found at all. Page might be blocked.")
            return False

    # Click and type
    search_box.click()
    time.sleep(1)
    search_box.clear()
    time.sleep(0.3)

    # Type the artist name
    for char in artist_name:
        search_box.send_keys(char)
        time.sleep(0.08)
    time.sleep(2)

    # ── Click the Search (submit) button ──
    search_clicked = False
    search_btn_selectors = [
        "button[type='submit']",
        "button.search-btn",
        "button[aria-label='Search']",
        "form button[type='submit']",
    ]
    for sel in search_btn_selectors:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if btn and btn.is_displayed():
                btn.click()
                print(f"   ✓ Clicked Search button (selector: {sel})")
                search_clicked = True
                break
        except Exception:
            continue

    if not search_clicked:
        # Fallback: press Enter to submit
        print("   ⚠ Search button not found — pressing Enter to submit...")
        search_box.send_keys(Keys.RETURN)

    # Wait for search results page to load
    time.sleep(5)

    print(f"   Results page URL: {driver.current_url}")

    # ── Click the first top-suggestion div that contains an <a> with href ──
    clicked = False

    # Strategy 1: CSS selectors targeting Top Suggestions section
    top_suggestion_selectors = [
        # Divs/cards in the Top Suggestions carousel — first link
        "div[class*='top-suggestion'] a[href]",
        "div[class*='TopSuggestion'] a[href]",
        "section[class*='suggestion'] a[href]",
        # Generic: first anchor with an artist/attraction href
        "a[href*='/artist/']",
        "a[href*='-tickets/artist/']",
        # Broader: first card-like div with a link
        "div[class*='card'] a[href*='ticketmaster']",
    ]

    for sel in top_suggestion_selectors:
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, sel)
            for elem in elems:
                href = elem.get_attribute("href") or ""
                if href and "ticketmaster.com" in href:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
                    time.sleep(0.5)
                    link_text = elem.text.strip() or href
                    elem.click()
                    print(f"   ✓ Clicked top suggestion: '{link_text}'")
                    clicked = True
                    break
            if clicked:
                break
        except Exception:
            continue

    # Strategy 2: JS fallback — find first div containing an <a> with href
    #             in the Top Suggestions area
    if not clicked:
        print("   Trying JS fallback for top suggestion...")
        try:
            clicked = driver.execute_script("""
                // Look for the first <a> with an href containing '/artist/' or '-tickets/'
                const links = document.querySelectorAll('a[href*="/artist/"], a[href*="-tickets/"]');
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    if (href && href.includes('ticketmaster.com')) {
                        link.click();
                        return true;
                    }
                }
                // Broader: any result link
                const allLinks = document.querySelectorAll('a[href*="ticketmaster.com"]');
                for (const link of allLinks) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('/artist/') || href.includes('-tickets/')) {
                        link.click();
                        return true;
                    }
                }
                return false;
            """)
            if clicked:
                print("   ✓ Clicked top suggestion via JS fallback")
        except Exception:
            pass

    if not clicked:
        print("   ✗ Could not click any top suggestion on search results page.")
        return False

    # Wait for artist page to load
    time.sleep(5)

    # Verify we're on an artist/event page
    current_url = driver.current_url
    print(f"   Current URL: {current_url}")

    if "ticketmaster.com" in current_url and current_url != TICKETMASTER_URL:
        print("   ✓ Navigated to artist/event page.")
        return True
    else:
        print("   ⚠ May not be on the correct page.")
        return True  # Try to scrape anyway


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPE CONCERT INFO
# ══════════════════════════════════════════════════════════════════════════════

def _click_load_more(driver, max_clicks=50):
    """Keep clicking 'Load More' / 'See More Events' until all events are visible."""
    for _ in range(max_clicks):
        try:
            btn = driver.find_element(By.XPATH,
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'more')]"
                " | //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'more event')]"
            )
            if btn.is_displayed() and btn.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.5)
                btn.click()
                print("   ↓ Clicked 'Load More'...")
                time.sleep(3)
            else:
                break
        except Exception:
            break


def scrape_concerts(driver, artist_name):
    """
    Scrape all concert rows from the current Ticketmaster artist page.
    Returns a list of row tuples matching the output CSV headers.
    """
    time.sleep(3)

    # Try to click "Load More" to reveal all events
    _click_load_more(driver)

    concerts = []

    # ── Strategy 1: parse structured event list items ──
    # Ticketmaster typically renders events as list-items or divs inside a
    # container.  We look for common patterns.
    event_items = []
    container_selectors = [
        "section ul li[class]",             # generic list items in a section
        "[data-testid*='event'] ",          # data-testid based
        ".event-listing__item",
        ".accordion__item",
        ".event-list li",
        "ul.event-list > li",
    ]
    for sel in container_selectors:
        try:
            items = driver.find_elements(By.CSS_SELECTOR, sel)
            if items and len(items) >= 1:
                event_items = items
                print(f"   Found {len(items)} event items via '{sel}'")
                break
        except Exception:
            continue

    if event_items:
        for item in event_items:
            try:
                text = item.text.strip()
                if not text or len(text) < 10:
                    continue
                row = _parse_event_text(text, artist_name)
                if row:
                    # Try to get event URL
                    try:
                        link = item.find_element(By.CSS_SELECTOR, "a[href*='event']")
                        row[-1] = link.get_attribute("href") or ""
                    except Exception:
                        try:
                            link = item.find_element(By.TAG_NAME, "a")
                            href = link.get_attribute("href") or ""
                            if "ticketmaster" in href:
                                row[-1] = href
                        except Exception:
                            pass
                    concerts.append(row)
            except Exception:
                continue

    # ── Strategy 2: JavaScript extraction ──
    if not concerts:
        print("   Trying JS-based extraction...")
        try:
            js_data = driver.execute_script("""
                const results = [];
                // Look for any element that contains event-like info
                const allLinks = document.querySelectorAll('a[href*="/event/"], a[href*="ticketmaster.com/"]');
                const seen = new Set();
                for (const a of allLinks) {
                    const container = a.closest('li') || a.closest('div[class*="event"]') || a.parentElement;
                    if (!container) continue;
                    const text = container.innerText.trim();
                    if (text.length < 15 || seen.has(text)) continue;
                    seen.add(text);
                    results.push({text: text, url: a.href});
                }
                return results;
            """)
            if js_data:
                print(f"   Found {len(js_data)} events via JS extraction")
                for item in js_data:
                    row = _parse_event_text(item["text"], artist_name)
                    if row:
                        row[-1] = item.get("url", "")
                        concerts.append(row)
        except Exception as e:
            print(f"   JS extraction failed: {e}")

    # ── Strategy 3: full page text parsing ──
    if not concerts:
        print("   Trying full-page text parsing...")
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
            concerts = _parse_full_page(page_text, artist_name)
            print(f"   Parsed {len(concerts)} events from page text")
        except Exception as e:
            print(f"   Full-page parse failed: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for row in concerts:
        key = (row[0], row[1], row[4], row[6])  # artist, date, venue, tour
        if key not in seen:
            seen.add(key)
            unique.append(row)

    print(f"   ✓ Total unique concerts scraped: {len(unique)}")
    return unique


def _parse_event_text(text, artist_name):
    """
    Parse the text block of a single event item into a row tuple.
    Expected patterns from the screenshot:
        PRESALE: 02/17/2026, 11:00 AM EST
        Sat • 8:00 PM ①
        Charlotte, NC • Spectrum Center
        J. Cole: The Fall-Off Tour
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return None

    date_str = ""
    day_of_week = ""
    event_time = ""
    city = ""
    state = ""
    venue = ""
    tour_name = ""
    presale_info = ""

    for line in lines:
        # Presale line
        if re.match(r'(?i)presale', line):
            presale_info = line.strip()
            continue

        # Date like "JUL\n11" or "JUL 11" — month abbreviation
        month_match = re.match(r'^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b', line, re.IGNORECASE)
        if month_match and not date_str:
            # Could be "JUL" alone (day on next line) or "JUL 11"
            rest = line[len(month_match.group(0)):].strip()
            if re.match(r'^\d{1,2}$', rest):
                date_str = f"{month_match.group(0).upper()} {rest}"
            else:
                date_str = month_match.group(0).upper()
            continue

        # Standalone day number (follows a month line)
        if re.match(r'^\d{1,2}$', line) and date_str and len(date_str) <= 3:
            date_str = f"{date_str} {line}"
            continue

        # Day + Time line: "Sat • 8:00 PM" or "Sat · 8:00 PM"
        day_time_match = re.match(
            r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s*[•·\-]\s*(\d{1,2}:\d{2}\s*[APap][Mm])',
            line, re.IGNORECASE
        )
        if day_time_match:
            day_of_week = day_time_match.group(1)
            event_time = day_time_match.group(2).strip()
            continue

        # City, State • Venue line
        city_venue_match = re.match(r'^(.+?),\s*([A-Z]{2})\s*[•·\-]\s*(.+)$', line)
        if city_venue_match:
            city = city_venue_match.group(1).strip()
            state = city_venue_match.group(2).strip()
            venue = city_venue_match.group(3).strip()
            continue

        # City, Country • Venue (international)
        city_venue_intl = re.match(r'^(.+?),\s*(.+?)\s*[•·\-]\s*(.+)$', line)
        if city_venue_intl and not city:
            city = city_venue_intl.group(1).strip()
            state = city_venue_intl.group(2).strip()
            venue = city_venue_intl.group(3).strip()
            continue

        # Tour / event name — usually contains the artist name or "Tour"
        if not tour_name and (
            artist_name.lower() in line.lower()
            or "tour" in line.lower()
            or "concert" in line.lower()
            or "live" in line.lower()
        ):
            tour_name = line.strip()
            continue

    # If we got at least city or venue, consider it valid
    if city or venue or date_str:
        return [
            artist_name,
            date_str,
            day_of_week,
            event_time,
            city,
            state,
            venue,
            tour_name,
            presale_info,
            "",  # event URL placeholder
        ]
    return None


def _parse_full_page(page_text, artist_name):
    """
    Fallback: split the full page text into event blocks and parse each.
    Events on Ticketmaster are separated by month headers (e.g., "JUL\\n11").
    """
    concerts = []
    # Split by month abbreviation at start of line
    blocks = re.split(
        r'\n(?=(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b)',
        page_text,
        flags=re.IGNORECASE
    )

    for block in blocks:
        block = block.strip()
        if not block or len(block) < 10:
            continue
        row = _parse_event_text(block, artist_name)
        if row:
            concerts.append(row)

    return concerts


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("   TICKETMASTER CONCERT SCRAPER")
    print("=" * 60)

    # ── Step 0: Ask user whether to use a proxy ──
    print("\nSelect run mode:")
    print("   [1] Run WITH proxy (find & apply a working proxy)")
    print("   [2] Run WITHOUT proxy (direct connection)")
    while True:
        choice = input("\nEnter 1 or 2: ").strip()
        if choice in ("1", "2"):
            break
        print("   Invalid input — please enter 1 or 2.")

    # ── Step 1: Find a working proxy (only if requested) ──
    proxy = None
    if choice == "1":
        proxy = find_working_proxy(max_tests=40)
        if proxy:
            print(f"\n✓ Using proxy: {proxy}")
        else:
            print("\n⚠ No working proxy found — proceeding without proxy.")
            print("  (Ticketmaster may block direct connections in some regions.)\n")
    else:
        print("\n→ Skipping proxy — using direct connection.\n")

    # ── Step 2: Read artist names from CSV ──
    headers, rows = read_csv(CSV_FILE)
    artist_col = headers.index("Artist Name")
    artist_names = [row[artist_col].strip() for row in rows if row[artist_col].strip()]

    if not artist_names:
        print("No artist names found in CSV. Exiting.")
        return

    print(f"\nArtists to scrape ({len(artist_names)}):")
    for name in artist_names:
        print(f"   • {name}")
    print()

    # ── Step 3: Delete old output file so we start fresh ──
    if os.path.isfile(OUTPUT_CSV):
        os.remove(OUTPUT_CSV)

    # ── Step 4: Launch browser & scrape ──
    driver = create_driver(proxy_addr=proxy)
    total_concerts = 0

    try:
        for idx, artist_name in enumerate(artist_names, 1):
            print(f"\n[{idx}/{len(artist_names)}] Processing: {artist_name}")

            success = search_artist_on_ticketmaster(driver, artist_name)
            if not success:
                print(f"   ✗ Could not navigate to {artist_name}'s page. Skipping.")
                continue

            concerts = scrape_concerts(driver, artist_name)
            if concerts:
                write_concerts_csv(OUTPUT_CSV, concerts)
                total_concerts += len(concerts)
                print(f"   ✓ Saved {len(concerts)} concerts for {artist_name}")
            else:
                print(f"   ⚠ No concerts found for {artist_name}")

            # Small delay between artists
            time.sleep(2)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user — saving progress...")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        traceback.print_exc()
    finally:
        try:
            driver.quit()
        except OSError:
            pass

    print(f"\n{'='*60}")
    print(f"   DONE — {total_concerts} concerts saved to:")
    print(f"   {OUTPUT_CSV}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()