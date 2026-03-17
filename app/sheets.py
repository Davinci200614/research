"""
Google Sheets helpers for writing completed job results.
"""

import re
from typing import List

import gspread
from google.oauth2.service_account import Credentials

from .config import settings
from .models import ArtistData

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_COLUMNS = [
    "artist_name",
    "genre",
    "tiktok_followers",
    "spotify_followers",
    "instagram_followers",
    "bandsintown_followers",
    "ig_username",
    "ig_engagement_rate",
    "tour_link",
    "venue_type",
    "soundcharts_url",
    "tm_profile_url",
    "first_presale_date",
    "first_onsale_date",
    "concerts_count",
]


def _normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())


def _sanitize_ws_title(name: str) -> str:
    # Google Sheets worksheet titles cannot include: : \ / ? * [ ]
    clean = re.sub(r"[:\\/?*\[\]]", " ", (name or "").strip())
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:100] or "Events"


def _unique_ws_title(sheet, base_title: str) -> str:
    existing = {ws.title for ws in sheet.worksheets()}
    if base_title not in existing:
        return base_title

    for i in range(2, 200):
        candidate = f"{base_title[:95]} {i}"
        if candidate not in existing:
            return candidate
    return f"{base_title[:90]} {str(len(existing) + 1)}"


def _create_concerts_sheet(sheet, artist: ArtistData, job_tag: str = "") -> str:
    data = artist.model_dump()
    concerts = data.get("concerts", []) or []
    artist_name = data.get("artist_name", "Artist") or "Artist"

    # Keep titles traceable while still short.
    base = f"Events - {_sanitize_ws_title(artist_name)}"
    if job_tag:
        base = f"{base} - {job_tag[:8]}"
    title = _unique_ws_title(sheet, _sanitize_ws_title(base))

    rows_needed = max(30, len(concerts) + 3)
    ws = sheet.add_worksheet(title=title, rows=rows_needed, cols=10)

    ws.update(
        "A1",
        [["TOUR DATES"]],
        value_input_option="RAW",
    )

    headers = [
        "Date",
        "Day",
        "Time",
        "City",
        "State",
        "Venue",
        "Tour / Event",
        "Presale / Onsale",
        "Link",
    ]
    ws.update("A2:I2", [headers], value_input_option="RAW")

    if concerts:
        rows = []
        for c in concerts:
            rows.append([
                str(c.get("date", "") or ""),
                str(c.get("day", "") or ""),
                str(c.get("time", "") or ""),
                str(c.get("city", "") or ""),
                str(c.get("state", "") or ""),
                str(c.get("venue", "") or ""),
                str(c.get("tour_name", "") or ""),
                str(c.get("presale_info", "") or ""),
                str(c.get("event_url", "") or ""),
            ])
        ws.update(f"A3:I{2 + len(rows)}", rows, value_input_option="RAW")

    return f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit#gid={ws.id}"


class SheetSyncError(RuntimeError):
    """Raised when sheet sync cannot be completed."""


def _get_client() -> gspread.Client:
    if not settings.google_sa_json:
        raise SheetSyncError("GOOGLE_SA_JSON is not configured")
    creds = Credentials.from_service_account_file(settings.google_sa_json, scopes=SCOPES)
    return gspread.authorize(creds)


def _ensure_header(worksheet) -> None:
    existing = worksheet.row_values(1)
    if not existing:
        worksheet.append_row(SHEET_COLUMNS, value_input_option="RAW")


def _result_to_row(artist: ArtistData, headers: List[str], event_sheet_url: str = "") -> List[str]:
    data = artist.model_dump()
    concerts_count = len(data.get("concerts", []) or [])

    # Flexible mapping for user-defined sheet headers.
    header_value = {
        "artistname": data.get("artist_name", ""),
        "basiclocation": "",
        "genre": data.get("genre", ""),
        "eventsheet": event_sheet_url,
        "venuetype": data.get("venue_type", ""),
        "linktotour": data.get("tour_link", ""),
        "ticketagenttour": data.get("tm_profile_url", ""),
        "gasection": "",
        "teammember": "",
        "prereportscompleted": "",
        "firstpresale": data.get("first_presale_date", ""),
        "gensale": data.get("first_onsale_date", ""),
        "tiktoklisteners": data.get("tiktok_followers", ""),
        "spotifylisteners": data.get("spotify_followers", ""),
        "instagramfollowers": data.get("instagram_followers", ""),
        "bandsintownfollowers": data.get("bandsintown_followers", ""),
        "30daygrowthpercentage": "",
        "igengagementrate": data.get("ig_engagement_rate", ""),
        "j": "",
        "j1": "",
        "j2": "",
        "status": "Completed",
        "buylist": "",
        "soundchartslink": data.get("soundcharts_url", ""),
        # Canonical API/table column names also supported.
        "artist_name": data.get("artist_name", ""),
        "tiktok_followers": data.get("tiktok_followers", ""),
        "spotify_followers": data.get("spotify_followers", ""),
        "instagram_followers": data.get("instagram_followers", ""),
        "bandsintown_followers": data.get("bandsintown_followers", ""),
        "ig_username": data.get("ig_username", ""),
        "ig_engagement_rate": data.get("ig_engagement_rate", ""),
        "tour_link": data.get("tour_link", ""),
        "venue_type": data.get("venue_type", ""),
        "soundcharts_url": data.get("soundcharts_url", ""),
        "tm_profile_url": data.get("tm_profile_url", ""),
        "first_presale_date": data.get("first_presale_date", ""),
        "first_onsale_date": data.get("first_onsale_date", ""),
        "concerts_count": str(concerts_count),
    }

    row: List[str] = []
    for header in headers:
        norm = _normalize_header(header)
        if norm in header_value:
            row.append(str(header_value[norm] or ""))
            continue

        # Fallback: match normalized model keys.
        value = ""
        for key, raw in data.items():
            if _normalize_header(key) == norm:
                value = raw
                break
        row.append(str(value or ""))

    return row


def append_results(results: List[ArtistData], job_id: str = "") -> int:
    if not settings.sheet_id:
        raise SheetSyncError("SHEET_ID is not configured")
    if not results:
        return 0

    client = _get_client()
    sheet = client.open_by_key(settings.sheet_id)
    worksheet = sheet.worksheet(settings.worksheet_name)

    _ensure_header(worksheet)
    headers = worksheet.row_values(1)
    rows = []
    for artist in results:
        event_sheet_url = _create_concerts_sheet(sheet, artist, job_tag=job_id)
        rows.append(_result_to_row(artist, headers, event_sheet_url=event_sheet_url))

    worksheet.append_rows(rows, value_input_option="RAW")
    return len(rows)


def get_sheet_url() -> str:
    if not settings.sheet_id:
        raise SheetSyncError("SHEET_ID is not configured")
    return f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit"
