"""
Ticketmaster concert scraper module.

Searches an artist on ticketmaster.com, navigates to their page,
and scrapes all listed concert / event details.
"""

import logging
import random
import re
import time
from typing import Dict, List, Optional, Union

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

TICKETMASTER_URL = "https://www.ticketmaster.com/"
SEARCH_INPUT_XPATH = '//*[@id="searchFormInput-input"]'

TM_COUNTRY_DOMAINS = {
    "USA": "ticketmaster.com",
    "CANADA": "ticketmaster.ca",
    "MEX": "ticketmaster.com.mx",
    "UK": "ticketmaster.co.uk",
}

FREE_PROXY_APIS = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&protocol=http&timeout=5000",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
]


# ══════════════════════════════════════════════════════════════════════════════
#  PROXY HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _load_local_proxies(proxy_list_file: str) -> List[str]:
    """Load proxies from a local text file."""
    import os

    proxies = []
    if os.path.isfile(proxy_list_file):
        with open(proxy_list_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", line):
                    if not line.startswith("0.0.0.0") and not line.startswith("127.0.0"):
                        proxies.append(line)
        logger.info("Loaded %d proxies from %s", len(proxies), proxy_list_file)
    return proxies


def _fetch_fresh_proxies() -> List[str]:
    """Fetch proxy lists from multiple free APIs."""
    all_proxies: set = set()
    for api_url in FREE_PROXY_APIS:
        try:
            resp = requests.get(api_url, timeout=10)
            if resp.status_code == 200:
                for line in resp.text.strip().splitlines():
                    line = re.sub(r"^https?://", "", line.strip())
                    if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", line):
                        if not line.startswith("0.0.0.0") and not line.startswith("127.0.0"):
                            all_proxies.add(line)
        except Exception:
            pass
    logger.info("Fetched %d unique online proxies", len(all_proxies))
    return list(all_proxies)


def _test_proxies(proxies: List[str], max_tests: int) -> Optional[str]:
    """Test proxies against Ticketmaster; return first working one."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/144.0.0.0 Safari/537.36"
        ),
    }
    random.shuffle(proxies)
    for i, proxy_addr in enumerate(proxies[: max_tests]):
        proxy_url = f"http://{proxy_addr}"
        px = {"http": proxy_url, "https": proxy_url}
        try:
            r = requests.get(TICKETMASTER_URL, proxies=px, headers=headers, timeout=10)
            if r.status_code == 200 and len(r.text) > 5000:
                logger.info("Working proxy found: %s", proxy_addr)
                return proxy_addr
        except Exception:
            pass
    return None


def find_working_proxy(proxy_list_file: str = "proxy_list.txt", max_tests: int = 40) -> Optional[str]:
    """Try local file first, then online sources. Returns working proxy or None."""
    local = _load_local_proxies(proxy_list_file)
    if local:
        result = _test_proxies(local, max_tests)
        if result:
            return result

    online = _fetch_fresh_proxies()
    if online:
        result = _test_proxies(online, max_tests)
        if result:
            return result

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  BROWSER
# ══════════════════════════════════════════════════════════════════════════════


def _parse_proxy(proxy_str: str):
    """Parse proxy string. Returns (host, port, username, password) or (host, port, None, None)."""
    parts = proxy_str.strip().split(":")
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    elif len(parts) == 2:
        return parts[0], parts[1], None, None
    return None


def _create_driver(chrome_version: int = 0, proxy_str: Optional[str] = None) -> uc.Chrome:
    """Create an undetected Chrome driver, optionally with an authenticated proxy.

    Uses a local relay thread for authenticated proxies since UC strips
    extension-based proxy auth in Chrome 145+.
    """
    import base64
    import socket
    import select
    import threading

    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")

    relay_thread = None
    relay_port = None

    if proxy_str:
        parsed = _parse_proxy(proxy_str)
        if parsed:
            host, port, user, pwd = parsed
            if user and pwd:
                # Start a local proxy relay to handle auth with upstream
                relay_port = _start_proxy_relay(host, int(port), user, pwd)
                options.add_argument(f"--proxy-server=http://127.0.0.1:{relay_port}")
                logger.info(
                    "Using authenticated proxy %s:%s via local relay on :%d",
                    host, port, relay_port,
                )
            else:
                options.add_argument(f"--proxy-server=http://{host}:{port}")
                logger.info("Using open proxy %s:%s", host, port)

    kwargs = {"options": options}
    if chrome_version:
        kwargs["version_main"] = chrome_version

    driver = uc.Chrome(**kwargs)
    return driver


def _start_proxy_relay(upstream_host: str, upstream_port: int,
                       username: str, password: str) -> int:
    """Start a local HTTP proxy that relays to an authenticated upstream proxy.

    Returns the local port number. The relay runs in daemon threads.
    """
    import base64
    import socket
    import select
    import threading
    import struct

    auth_header = base64.b64encode(f"{username}:{password}".encode()).decode()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    local_port = server_sock.getsockname()[1]
    server_sock.listen(32)

    def _pipe(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                src.close()
            except Exception:
                pass
            try:
                dst.close()
            except Exception:
                pass

    def _handle_client(client_sock):
        try:
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = client_sock.recv(4096)
                if not chunk:
                    client_sock.close()
                    return
                raw += chunk

            first_line = raw.split(b"\r\n")[0].decode("utf-8", errors="replace")
            parts = first_line.split()
            method = parts[0] if parts else ""

            # Connect to upstream proxy
            upstream = socket.create_connection((upstream_host, upstream_port), timeout=30)

            if method == "CONNECT":
                # HTTPS tunneling — send CONNECT to upstream with auth
                target = parts[1] if len(parts) > 1 else ""
                connect_req = (
                    f"CONNECT {target} HTTP/1.1\r\n"
                    f"Host: {target}\r\n"
                    f"Proxy-Authorization: Basic {auth_header}\r\n"
                    f"\r\n"
                ).encode()
                upstream.sendall(connect_req)

                # Read upstream response
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = upstream.recv(4096)
                    if not chunk:
                        break
                    resp += chunk

                if b"200" in resp.split(b"\r\n")[0]:
                    client_sock.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    # Bidirectional relay
                    t1 = threading.Thread(target=_pipe, args=(client_sock, upstream), daemon=True)
                    t2 = threading.Thread(target=_pipe, args=(upstream, client_sock), daemon=True)
                    t1.start()
                    t2.start()
                    t1.join()
                    t2.join()
                else:
                    client_sock.sendall(resp)
                    client_sock.close()
                    upstream.close()
            else:
                # HTTP request — inject auth header and forward
                header_end = raw.index(b"\r\n\r\n")
                headers_part = raw[:header_end]
                body_part = raw[header_end:]
                # Inject Proxy-Authorization header
                auth_line = f"Proxy-Authorization: Basic {auth_header}".encode()
                modified = headers_part + b"\r\n" + auth_line + body_part
                upstream.sendall(modified)

                # Relay the response back
                _pipe(upstream, client_sock)

        except Exception:
            try:
                client_sock.close()
            except Exception:
                pass

    def _accept_loop():
        while True:
            try:
                client_sock, _ = server_sock.accept()
                threading.Thread(
                    target=_handle_client, args=(client_sock,), daemon=True
                ).start()
            except Exception:
                break

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    logger.info("Proxy relay started on 127.0.0.1:%d → %s:%d", local_port, upstream_host, upstream_port)
    return local_port


# ══════════════════════════════════════════════════════════════════════════════
#  SEARCH & NAVIGATE
# ══════════════════════════════════════════════════════════════════════════════


def _dismiss_overlays(driver):
    """Dismiss cookie consent banners and other overlays."""
    for sel in [
        "#onetrust-accept-btn-handler",
        "button[id*='accept']",
        "button[class*='consent']",
        "button[aria-label*='Accept']",
        "button[aria-label*='accept']",
        "button[aria-label*='Close']",
        "#onetrust-close-btn-container button",
    ]:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if btn.is_displayed():
                btn.click()
                logger.info("Dismissed overlay: %s", sel)
                time.sleep(1)
                break
        except Exception:
            continue


def _search_artist(driver, artist_name: str, tm_country: str = "USA") -> bool:
    """
    Navigate to the Ticketmaster search results for the artist,
    then click the first artist/attraction link. Returns True on success.
    """
    from urllib.parse import quote_plus

    country = (tm_country or "USA").upper()
    domain = TM_COUNTRY_DOMAINS.get(country, TM_COUNTRY_DOMAINS["USA"])

    # Go directly to search results URL — avoids SPA input interaction issues.
    search_url = f"https://{domain}/search?q={quote_plus(artist_name)}"
    logger.info("Navigating to search URL: %s", search_url)
    driver.get(search_url)
    time.sleep(8)

    logger.info("Page title: '%s' | URL: %s", driver.title, driver.current_url)

    # Wait for the page to fully render (SPA)
    for _ in range(5):
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if len(body_text) > 200:
                break
        except Exception:
            pass
        time.sleep(3)

    _dismiss_overlays(driver)
    time.sleep(1)

    # Click the first artist/event link on the results page
    clicked = False

    # Strategy 1: CSS selectors for search result links
    result_selectors = [
        "a[href*='/artist/']",
        "a[href*='-tickets/artist/']",
        "div[class*='top-suggestion'] a[href]",
        "div[class*='TopSuggestion'] a[href]",
        "section[class*='suggestion'] a[href]",
        "div[class*='card'] a[href*='ticketmaster']",
        "a[href*='/event/']",
    ]
    for sel in result_selectors:
        try:
            for elem in driver.find_elements(By.CSS_SELECTOR, sel):
                href = elem.get_attribute("href") or ""
                if href and "ticketmaster" in href:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", elem
                    )
                    time.sleep(0.5)
                    link_text = elem.text.strip() or href
                    try:
                        elem.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", elem)
                    logger.info("Clicked result: '%s' (%s)", link_text[:80], sel)
                    clicked = True
                    break
            if clicked:
                break
        except Exception:
            continue

    # Strategy 2: JS fallback — find first link to an artist page
    if not clicked:
        try:
            clicked = driver.execute_script("""
                const links = document.querySelectorAll(
                    'a[href*="/artist/"], a[href*="-tickets/"]'
                );
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('ticketmaster')) {
                        link.click(); return true;
                    }
                }
                const all = document.querySelectorAll('a[href*="ticketmaster"]');
                for (const link of all) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('/artist/') || href.includes('-tickets/')) {
                        link.click(); return true;
                    }
                }
                return false;
            """)
            if clicked:
                logger.info("Clicked result via JS fallback")
        except Exception:
            pass

    if not clicked:
        logger.warning("Could not click any result for %s", artist_name)
        # Log what links exist on the page for debugging
        try:
            link_count = driver.execute_script(
                "return document.querySelectorAll('a[href]').length;"
            )
            logger.warning("Total links on page: %d", link_count)
        except Exception:
            pass
        return False

    time.sleep(5)
    current_url = driver.current_url
    logger.info("Navigated to %s for artist %s", current_url, artist_name)
    return "ticketmaster" in current_url and current_url != TICKETMASTER_URL


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPE CONCERTS
# ══════════════════════════════════════════════════════════════════════════════


def _click_load_more(driver, max_clicks: int = 100):
    """Click 'More Events' until all domestic events are loaded."""
    for i in range(max_clicks):
        try:
            # Look for "More Events" button specifically
            btn = driver.execute_script("""
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    const txt = b.innerText.trim().toLowerCase();
                    if (txt.includes('more event') || txt === 'more events') {
                        return b;
                    }
                }
                return null;
            """)
            if not btn:
                logger.info("No more 'More Events' button found — all events loaded")
                break
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", btn
            )
            time.sleep(0.5)
            try:
                btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
            logger.info("Clicked 'More Events' (%d)", i + 1)
            time.sleep(3)
        except Exception:
            break


def _parse_event_text(text: str, artist_name: str) -> Optional[List[str]]:
    """Parse a single event text block into a list of fields."""
    lower = text.lower()
    if 'on partner site' in lower:
        return None
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
    presale_date = ""
    onsale_date = ""

    for line in lines:
        # PRESALE: 03/12/2026, 10:00 AM CDT  or  PRESALE HAPPENING NOW
        pm = re.match(r"(?i)presale[:\s]*(.*)", line)
        if pm:
            presale_info = line.strip()
            dp = pm.group(1).strip()
            if re.search(r"\d{2}/\d{2}/\d{4}", dp):
                presale_date = dp
            elif re.search(r"(?i)happening\s+now", dp):
                presale_date = "HAPPENING NOW"
            continue

        # GENERAL ONSALE: 03/13/2026, 10:00 AM GMT+1
        om = re.match(r"(?i)general\s+on[-\s]*sale[:\s]*(.*)", line)
        if om:
            onsale_line = line.strip()
            presale_info = (presale_info + " | " + onsale_line) if presale_info else onsale_line
            dp = om.group(1).strip()
            if re.search(r"\d{2}/\d{2}/\d{4}", dp):
                onsale_date = dp
            elif re.search(r"(?i)happening\s+now", dp):
                onsale_date = "HAPPENING NOW"
            continue

        month_match = re.match(
            r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b",
            line,
            re.IGNORECASE,
        )
        if month_match and not date_str:
            rest = line[len(month_match.group(0)) :].strip()
            if re.match(r"^\d{1,2}$", rest):
                date_str = f"{month_match.group(0).upper()} {rest}"
            else:
                date_str = month_match.group(0).upper()
            continue

        if re.match(r"^\d{1,2}$", line) and date_str and len(date_str) <= 3:
            date_str = f"{date_str} {line}"
            continue

        day_time_match = re.match(
            r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s*[•·\-]\s*(\d{1,2}:\d{2}\s*[APap][Mm])",
            line,
            re.IGNORECASE,
        )
        if day_time_match:
            day_of_week = day_time_match.group(1)
            event_time = day_time_match.group(2).strip()
            continue

        city_venue_match = re.match(r"^(.+?),\s*([A-Z]{2})\s*[•·\-]\s*(.+)$", line)
        if city_venue_match:
            city = city_venue_match.group(1).strip()
            state = city_venue_match.group(2).strip()
            venue = city_venue_match.group(3).strip()
            continue

        # City, ST, Venue (comma-separated — newer TM layout)
        city_comma_match = re.match(r"^(.+?),\s*([A-Z]{2})\s*,\s*(.+)$", line)
        if city_comma_match and not city:
            city = city_comma_match.group(1).strip()
            state = city_comma_match.group(2).strip()
            # Venue may include trailing "Artist - Tour Name"; keep only first part
            raw_venue = city_comma_match.group(3).strip()
            # Split on comma — venue is typically the first segment
            venue_parts = raw_venue.split(",")
            venue = venue_parts[0].strip()
            continue

        city_venue_intl = re.match(r"^(.+?),\s*(.+?)\s*[•·\-]\s*(.+)$", line)
        if city_venue_intl and not city:
            city = city_venue_intl.group(1).strip()
            state = city_venue_intl.group(2).strip()
            venue = city_venue_intl.group(3).strip()
            continue

        if not tour_name and (
            artist_name.lower() in line.lower()
            or "tour" in line.lower()
            or "concert" in line.lower()
            or "live" in line.lower()
        ):
            tour_name = line.strip()
            continue

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
            presale_date,
            onsale_date,
            "",  # event URL placeholder
        ]
    return None


def _parse_full_page(page_text: str, artist_name: str) -> List[List[str]]:
    """Fallback: split full page text into event blocks and parse each."""
    concerts = []
    blocks = re.split(
        r"\n(?=(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b)",
        page_text,
        flags=re.IGNORECASE,
    )
    for block in blocks:
        block = block.strip()
        if not block or len(block) < 10:
            continue
        row = _parse_event_text(block, artist_name)
        if row:
            concerts.append(row)
    return concerts


def _scrape_concerts(driver, artist_name: str) -> List[Dict[str, str]]:
    """Scrape all concert rows from the current artist page.

    Clicks 'More Events' to load all events, stops at 'International Concerts',
    and only returns events that have presale information.
    """
    time.sleep(3)
    _click_load_more(driver)

    raw_rows: List[List[str]] = []

    # Strategy 1: JS-based extraction that stops at International Concerts
    try:
        js_data = driver.execute_script("""
            const results = [];
            const body = document.body;

            // Find the "International Concerts" heading to know where to stop
            let intlBoundary = null;
            const headings = document.querySelectorAll('h2, h3, h4, [class*="heading"], [class*="title"]');
            for (const h of headings) {
                if (h.innerText && h.innerText.trim().toLowerCase().includes('international')) {
                    intlBoundary = h;
                    break;
                }
            }

            // Collect all event links, stop if we pass intlBoundary
            const allLinks = document.querySelectorAll('a[href*="/event/"], a[href*="ticketmaster.com/"]');
            const seen = new Set();
            for (const a of allLinks) {
                // Check if this link is inside or after the international section
                if (intlBoundary) {
                    const pos = intlBoundary.compareDocumentPosition(a);
                    // Node.DOCUMENT_POSITION_FOLLOWING = 4
                    if (pos & 4) break;  // a comes after intlBoundary -> stop
                }

                const container = a.closest('li')
                    || a.closest('div[class*="event"]')
                    || a.parentElement;
                if (!container) continue;
                const text = container.innerText.trim();
                if (text.length < 15 || seen.has(text)) continue;
                seen.add(text);
                results.push({text: text, url: a.href});
            }
            return results;
        """)
        if js_data:
            for item in js_data:
                # Skip items that are part of international section
                if 'international' in item['text'].lower()[:50]:
                    continue
                row = _parse_event_text(item["text"], artist_name)
                if row:
                    row[-1] = item.get("url", "")
                    raw_rows.append(row)
    except Exception:
        pass

    # Strategy 2: structured event items (fallback)
    if not raw_rows:
        event_items = []
        for sel in [
            "section ul li[class]",
            "[data-testid*='event']",
            ".event-listing__item",
            ".accordion__item",
            ".event-list li",
            "ul.event-list > li",
        ]:
            try:
                items = driver.find_elements(By.CSS_SELECTOR, sel)
                if items:
                    event_items = items
                    break
            except Exception:
                continue

        hit_international = False
        for item in event_items:
            try:
                text = item.text.strip()
                if not text or len(text) < 10:
                    continue
                # Stop at International Concerts section
                if 'international' in text.lower()[:50]:
                    hit_international = True
                    break
                if hit_international:
                    break
                row = _parse_event_text(text, artist_name)
                if row:
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
                    raw_rows.append(row)
            except Exception:
                continue

    # Strategy 3: full page text parsing (fallback)
    if not raw_rows:
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
            # Cut off at International Concerts
            intl_idx = page_text.lower().find('international concert')
            if intl_idx > 0:
                page_text = page_text[:intl_idx]
            raw_rows = _parse_full_page(page_text, artist_name)
        except Exception:
            pass

    # Deduplicate
    seen: set = set()
    unique: List[List[str]] = []
    for row in raw_rows:
        key = (row[0], row[1], row[4], row[6])
        if key not in seen:
            seen.add(key)
            unique.append(row)

    # Convert to dicts
    keys = [
        "artist_name", "date", "day", "time", "city", "state",
        "venue", "tour_name", "presale_info", "presale_date",
        "onsale_date", "event_url",
    ]
    all_concerts = [dict(zip(keys, row)) for row in unique]

    # Only keep events with presale or general-onsale info
    with_sale = [
        c for c in all_concerts
        if c.get("presale_info") or c.get("onsale_date")
    ]
    logger.info(
        "Filtered %d/%d events with sale info",
        len(with_sale), len(all_concerts),
    )
    return with_sale


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════


def scrape_ticketmaster_concerts(
    artist_inputs: List[Union[str, Dict[str, str]]],
    chrome_version: int = 0,
    proxy_str: Optional[str] = None,
) -> Dict[str, Dict]:
    """
    Scrape Ticketmaster concerts for a list of artists.

    Returns ``{artist_name: {"concerts": [...], "tm_profile_url": "...",
    "first_presale_date": "...", "first_onsale_date": "..."}, ...}``.
    """
    _empty: Dict = {
        "concerts": [],
        "tm_profile_url": "",
        "first_presale_date": "",
        "first_onsale_date": "",
    }
    results: Dict[str, Dict] = {}
    driver = _create_driver(chrome_version=chrome_version, proxy_str=proxy_str)

    try:
        for artist in artist_inputs:
            if isinstance(artist, dict):
                artist_name = (artist.get("artist_name") or "").strip()
                tm_country = (artist.get("country") or "USA").upper()
            else:
                artist_name = str(artist).strip()
                tm_country = "USA"

            if not artist_name:
                continue

            logger.info("Ticketmaster: searching %s (country=%s)", artist_name, tm_country)
            success = _search_artist(driver, artist_name, tm_country=tm_country)
            if not success:
                logger.warning("Ticketmaster: could not navigate to %s", artist_name)
                results[artist_name] = {**_empty}
                continue

            profile_url = driver.current_url
            concerts = _scrape_concerts(driver, artist_name)

            first_presale = ""
            first_onsale = ""
            for c in concerts:
                if not first_presale and c.get("presale_date"):
                    first_presale = c["presale_date"]
                if not first_onsale and c.get("onsale_date"):
                    first_onsale = c["onsale_date"]
                if first_presale and first_onsale:
                    break

            results[artist_name] = {
                "concerts": concerts,
                "tm_profile_url": profile_url,
                "first_presale_date": first_presale,
                "first_onsale_date": first_onsale,
            }
            logger.info(
                "Ticketmaster: %d concerts for %s", len(concerts), artist_name
            )
            time.sleep(2)
    except Exception as exc:
        logger.exception("Ticketmaster scraper error: %s", exc)
    finally:
        try:
            driver.quit()
        except OSError:
            pass

    return results
