"""
FastAPI application — Soundcharts Artist Research API.

Start with::

    uvicorn app.main:app --reload

Or via the convenience runner::

    python run.py
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .jobs import JobManager
from .models import (
    ArtistData,
    HealthResponse,
    JobResponse,
    SheetSyncResponse,
    JobStatus,
    ScrapeRequest,
    ScrapeStartResponse,
)
from .sheets import SheetSyncError, append_results, get_sheet_url

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)

# ── Job manager (singleton for the process) ──────────────────────────────────

job_manager = JobManager()

# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.getLogger(__name__).info(
        "API starting  (headless=%s, chrome_version=%d)",
        settings.headless,
        settings.chrome_version,
    )
    yield


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Soundcharts Artist Research API",
    version="1.0.0",
    description=(
        "REST API for researching artists via Soundcharts. "
        "Extracts follower counts, genre, IG engagement rate, "
        "tour links, and venue types."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files & SPA root ─────────────────────────────────────────────────

_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", include_in_schema=False)
def root():
    """Serve the single-page web app."""
    index = _static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"detail": "Web UI not found. Visit /docs for API documentation."}


# ══════════════════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


@app.get("/api/v1/health", response_model=HealthResponse, tags=["Health"])
def health():
    """Service health check."""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        headless=settings.headless,
    )


@app.post("/api/v1/scrape", response_model=ScrapeStartResponse, tags=["Scrape"])
def start_scrape(body: ScrapeRequest):
    """
    Start a background research job.

    The pipeline:
      1. Login to Soundcharts → search each artist → extract followers,
         genre, IG username, Soundcharts URL.
      2. (optional) Fetch tour link via OpenAI web search.
      3. (optional) Fetch venue type via OpenAI web search.
      4. (optional) Fetch IG engagement rate from TrendHero
         (uses undetected Chrome + CAPTCHA solving).

    Returns a ``job_id`` to poll via ``GET /api/v1/jobs/{job_id}``.
    """
    try:
        job_id = job_manager.create(
            artists=body.artists,
            ticketmaster_country_map=body.ticketmaster_country_map,
            skip_existing=body.skip_existing,
            include_engagement=body.include_engagement,
            include_tour_link=body.include_tour_link,
            include_venue_type=body.include_venue_type,
            include_ticketmaster=body.include_ticketmaster,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ScrapeStartResponse(
        job_id=job_id,
        status=JobStatus.QUEUED,
        message=f"Research job queued for {len(body.artists)} artist(s)",
    )


@app.get("/api/v1/jobs", response_model=List[JobResponse], tags=["Jobs"])
def list_jobs():
    """List all jobs (newest first)."""
    jobs = job_manager.list_all()
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [_job_to_response(j) for j in jobs]


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse, tags=["Jobs"])
def get_job(job_id: str):
    """Get status, progress, and (when complete) full results for a job."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_response(job)


@app.delete("/api/v1/jobs/{job_id}", tags=["Jobs"])
def delete_job(job_id: str):
    """Remove a finished job from the in-memory store."""
    if not job_manager.delete(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"detail": "deleted"}


@app.post(
    "/api/v1/jobs/{job_id}/sync-sheet",
    response_model=SheetSyncResponse,
    tags=["Jobs"],
)
def sync_job_to_sheet(job_id: str):
    """Append completed job results to configured Google Sheet."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Job is not completed yet",
        )

    try:
        rows_written = append_results(job.result, job_id=job_id)
        return SheetSyncResponse(
            job_id=job_id,
            rows_written=rows_written,
            sheet_url=get_sheet_url(),
            worksheet_name=settings.worksheet_name,
            message=f"Synced {rows_written} row(s) to Google Sheet",
        )
    except SheetSyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sheet sync failed: {exc}") from exc


# ── Helpers ──────────────────────────────────────────────────────────────────


def _job_to_response(job) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        progress=job.progress,
        result=job.result if job.status == JobStatus.COMPLETED else None,
        error=job.error,
    )


