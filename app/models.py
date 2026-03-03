"""
Pydantic models for API request / response schemas.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

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


# ── Data ──


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


class HealthResponse(BaseModel):
    status: str
    version: str
    headless: bool
