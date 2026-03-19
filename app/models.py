"""
Pydantic models for API request / response schemas.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enums ──


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Request ──


class ScrapeRequest(BaseModel):
    """Body for ``POST /api/v1/scrape``."""

    artists: List[str] = Field(
        ..., min_length=1, description="Artist names to scrape"
    )
    ticketmaster_country_map: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional per-artist Ticketmaster country target (USA, CANADA, MEX, UK)",
    )
    skip_existing: bool = Field(
        True, description="Skip artists that already have data"
    )
    include_engagement: bool = Field(
        True, description="Fetch IG engagement rate from TrendHero"
    )
    include_tour_link: bool = Field(
        True, description="Fetch tour / website link via OpenAI"
    )
    include_venue_type: bool = Field(
        True, description="Fetch venue type via OpenAI"
    )
    include_ticketmaster: bool = Field(
        True, description="Fetch concert listings from Ticketmaster"
    )


# ── Data ──


class ConcertData(BaseModel):
    date: str = ""
    day: str = ""
    time: str = ""
    city: str = ""
    state: str = ""
    venue: str = ""
    tour_name: str = ""
    presale_info: str = ""
    presale_date: str = ""
    onsale_date: str = ""
    event_url: str = ""


class ArtistData(BaseModel):
    artist_name: str = ""
    genre: str = ""
    tiktok_followers: str = ""
    spotify_followers: str = ""
    instagram_followers: str = ""
    bandsintown_followers: str = ""
    ig_username: str = ""
    ig_engagement_rate: str = ""
    tour_link: str = ""
    venue_type: str = ""
    soundcharts_url: str = ""
    tm_profile_url: str = ""
    first_presale_date: str = ""
    first_onsale_date: str = ""
    concerts: List[ConcertData] = []


class JobProgress(BaseModel):
    total_artists: int = 0
    completed_artists: int = 0
    current_artist: Optional[str] = None
    current_step: str = ""


# ── Response ──


class ScrapeStartResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    progress: JobProgress
    result: Optional[List[ArtistData]] = None
    error: Optional[str] = None


class SheetSyncResponse(BaseModel):
    job_id: str
    sheet_url: str
    rows_written: int
    worksheet_name: str
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str
    headless: bool
