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
    RuntimeConfigResponse,
    RuntimeConfigUpdateRequest,
    SheetSyncResponse,
    JobStatus,
    ScrapeRequest,
    ScrapeStartResponse,
)
from .sheets import SheetSyncError, append_results, get_sheet_url

_ENV_KEY_MAP = {
    "mail_address": "MAIL_ADDRESS",
    "mail_password": "MAIL_PASSWORD",
    "mail_address1": "MAIL_ADDRESS1",
    "mail_password1": "MAIL_PASSWORD1",
    "openai_api_key": "OPENAI_API_KEY",
    "sheet_id": "SHEET_ID",
    "worksheet_name": "WORKSHEET_NAME",
    "google_sa_json": "GOOGLE_SA_JSON",
    "tm_proxy": "TM_PROXY",
    "headless": "HEADLESS",
    "chrome_version": "CHROME_VERSION",
    "max_concurrent_jobs": "MAX_CONCURRENT_JOBS",
    "disable_engagement_in_headless": "DISABLE_ENGAGEMENT_IN_HEADLESS",
}

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


@app.get("/api/v1/settings", response_model=RuntimeConfigResponse, tags=["Settings"])
def get_runtime_settings():
    """Get runtime configuration used by new jobs."""
    return _settings_to_response()


@app.put("/api/v1/settings", response_model=RuntimeConfigResponse, tags=["Settings"])
def update_runtime_settings(body: RuntimeConfigUpdateRequest):
    """Update runtime configuration without editing .env file."""
    payload = body.model_dump(exclude_unset=True)
    persist_to_env = bool(payload.pop("persist_to_env", False))
    for key, value in payload.items():
        setattr(settings, key, value)

    if persist_to_env and payload:
        _persist_settings_to_env(payload)

    return _settings_to_response(persisted_to_env=persist_to_env)


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


def _settings_to_response(persisted_to_env: bool = False) -> RuntimeConfigResponse:
    return RuntimeConfigResponse(
        mail_address=settings.mail_address,
        mail_address1=settings.mail_address1,
        openai_api_key_set=bool(settings.openai_api_key),
        mail_password_set=bool(settings.mail_password),
        mail_password1_set=bool(settings.mail_password1),
        sheet_id=settings.sheet_id,
        worksheet_name=settings.worksheet_name,
        google_sa_json=settings.google_sa_json,
        google_sa_json_set=bool(settings.google_sa_json),
        tm_proxy_set=bool(settings.tm_proxy),
        tm_proxy=settings.tm_proxy,
        headless=settings.headless,
        chrome_version=settings.chrome_version,
        max_concurrent_jobs=settings.max_concurrent_jobs,
        disable_engagement_in_headless=settings.disable_engagement_in_headless,
        persisted_to_env=persisted_to_env,
    )


def _persist_settings_to_env(updates: dict) -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    for key, value in updates.items():
        env_key = _ENV_KEY_MAP.get(key)
        if not env_key:
            continue

        if isinstance(value, bool):
            val_str = "true" if value else "false"
        else:
            val_str = str(value)

        new_line = f"{env_key}={val_str}"
        replaced = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{env_key}="):
                lines[i] = new_line
                replaced = True
                break
        if not replaced:
            lines.append(new_line)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
