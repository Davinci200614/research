"""
In-memory background job manager for the Soundcharts pipeline.

Each scrape request runs in its own daemon thread.  Job state is stored
in a thread-safe dict.

Pipeline per artist:
  1. Soundcharts login → search → extract followers, genre, IG username
  2. (optional) TrendHero engagement rate via undetected Chrome + CAPTCHA
  3. (optional) Tour link via OpenAI web search
  4. (optional) Venue type via OpenAI web search

**For production at scale**, swap this with Celery + Redis/RabbitMQ.
"""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import settings
from .models import ArtistData, JobProgress, JobStatus

logger = logging.getLogger(__name__)


class Job:
    """Internal mutable job state."""

    def __init__(
        self,
        job_id: str,
        artists: List[str],
        skip_existing: bool,
        include_engagement: bool,
        include_tour_link: bool,
        include_venue_type: bool,
    ):
        self.job_id = job_id
        self.artists = artists
        self.skip_existing = skip_existing
        self.include_engagement = include_engagement
        self.include_tour_link = include_tour_link
        self.include_venue_type = include_venue_type

        self.status: JobStatus = JobStatus.QUEUED
        self.created_at: datetime = datetime.now(timezone.utc)
        self.updated_at: datetime = datetime.now(timezone.utc)
        self.progress: JobProgress = JobProgress(total_artists=len(artists))
        self.result: List[ArtistData] = []
        self.error: Optional[str] = None


class JobManager:
    """Create, track, and run background scrape jobs."""

    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    # ── CRUD ─────────────────────────────────────────────────────────────

    def create(
        self,
        artists: List[str],
        skip_existing: bool = True,
        include_engagement: bool = True,
        include_tour_link: bool = True,
        include_venue_type: bool = True,
    ) -> str:
        job_id = uuid.uuid4().hex[:8]
        job = Job(
            job_id, artists, skip_existing,
            include_engagement, include_tour_link, include_venue_type,
        )
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job_id,), daemon=True)
        thread.start()
        return job_id

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_all(self) -> List[Job]:
        return list(self._jobs.values())

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.pop(job_id, None) is not None

    # ── Worker ───────────────────────────────────────────────────────────

    def _run(self, job_id: str):
        job = self._jobs[job_id]
        job.status = JobStatus.RUNNING
        job.updated_at = datetime.now(timezone.utc)

        try:
            from .scrapers.soundcharts import SoundchartsScraper

            if not settings.mail_address or not settings.mail_password:
                raise RuntimeError(
                    "MAIL_ADDRESS / MAIL_PASSWORD not set in .env"
                )

            # ── Phase 1: Soundcharts (headless Chrome) ──
            job.progress.current_step = "soundcharts"
            collected: List[dict] = []  # raw dicts per artist

            with SoundchartsScraper(
                email=settings.mail_address,
                password=settings.mail_password,
                headless=settings.headless,
            ) as sc:
                if not sc.login():
                    raise RuntimeError("Soundcharts login failed")

                import time
                time.sleep(3)

                for idx, artist in enumerate(job.artists):
                    job.progress.current_artist = artist
                    job.progress.current_step = f"soundcharts: {artist}"
                    job.updated_at = datetime.now(timezone.utc)

                    logger.info("[%d/%d] %s", idx + 1, len(job.artists), artist)
                    follower_data, ig_username, sc_url = sc.process_artist(artist)

                    entry = {
                        "artist_name": artist,
                        "genre": follower_data.get("genre", ""),
                        "tiktok_followers": follower_data.get("tiktok_followers", ""),
                        "spotify_followers": follower_data.get("spotify_followers", ""),
                        "instagram_followers": follower_data.get("instagram_followers", ""),
                        "bandsintown_followers": follower_data.get("bandsintown_followers", ""),
                        "ig_username": ig_username or "",
                        "ig_engagement_rate": "",
                        "tour_link": "",
                        "venue_type": "",
                        "soundcharts_url": sc_url,
                    }
                    collected.append(entry)
                    job.progress.completed_artists = idx + 1
                    job.updated_at = datetime.now(timezone.utc)
                    time.sleep(2)

            # ── Phase 2: Tour links (OpenAI, no browser) ──
            if job.include_tour_link and settings.openai_api_key:
                from .scrapers.openai_tools import get_tour_link
                import time

                job.progress.current_step = "tour_links"
                job.updated_at = datetime.now(timezone.utc)

                for entry in collected:
                    name = entry["artist_name"]
                    job.progress.current_artist = name
                    job.updated_at = datetime.now(timezone.utc)
                    link = get_tour_link(name, settings.openai_api_key)
                    if link:
                        entry["tour_link"] = link
                    time.sleep(1)

            # ── Phase 3: Venue types (OpenAI, no browser) ──
            if job.include_venue_type and settings.openai_api_key:
                from .scrapers.openai_tools import get_venue_type
                import time

                job.progress.current_step = "venue_types"
                job.updated_at = datetime.now(timezone.utc)

                for entry in collected:
                    name = entry["artist_name"]
                    job.progress.current_artist = name
                    job.updated_at = datetime.now(timezone.utc)
                    vt = get_venue_type(name, settings.openai_api_key)
                    if vt:
                        entry["venue_type"] = vt
                    time.sleep(1)

            # ── Phase 4: Engagement rates (undetected Chrome + CAPTCHA) ──
            if job.include_engagement:
                from .scrapers.engagement import get_engagement_rate_batch
                import time

                ig_map = {
                    e["ig_username"]: e
                    for e in collected
                    if e["ig_username"]
                }
                if ig_map:
                    job.progress.current_step = "engagement_rates"
                    job.updated_at = datetime.now(timezone.utc)

                    try:
                        er_results = get_engagement_rate_batch(
                            list(ig_map.keys()),
                            chrome_version=settings.chrome_version,
                        )
                        for username, er in er_results.items():
                            if er:
                                ig_map[username]["ig_engagement_rate"] = er
                    except Exception as er_exc:
                        logger.warning(
                            "Job %s: engagement phase failed (results so far preserved): %s",
                            job_id, er_exc,
                        )

            # ── Finalize ──
            job.result = [ArtistData(**e) for e in collected]
            job.progress.current_artist = None
            job.progress.current_step = "done"
            job.status = JobStatus.COMPLETED
            logger.info("Job %s completed — %d artists", job_id, len(collected))

        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)

        job.updated_at = datetime.now(timezone.utc)
