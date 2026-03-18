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
from .models import ArtistData, ConcertData, JobProgress, JobStatus

logger = logging.getLogger(__name__)


class Job:
    """Internal mutable job state."""

    def __init__(
        self,
        job_id: str,
        artists: List[str],
        ticketmaster_country_map: Dict[str, str],
        skip_existing: bool,
        include_engagement: bool,
        include_tour_link: bool,
        include_venue_type: bool,
        include_ticketmaster: bool,
    ):
        self.job_id = job_id
        self.artists = artists
        self.ticketmaster_country_map = ticketmaster_country_map
        self.skip_existing = skip_existing
        self.include_engagement = include_engagement
        self.include_tour_link = include_tour_link
        self.include_venue_type = include_venue_type
        self.include_ticketmaster = include_ticketmaster

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
        ticketmaster_country_map: Optional[Dict[str, str]] = None,
        skip_existing: bool = True,
        include_engagement: bool = True,
        include_tour_link: bool = True,
        include_venue_type: bool = True,
        include_ticketmaster: bool = True,
    ) -> str:
        job_id = uuid.uuid4().hex[:8]
        job = Job(
            job_id, artists, ticketmaster_country_map or {}, skip_existing,
            include_engagement, include_tour_link, include_venue_type,
            include_ticketmaster,
        )
        with self._lock:
            active_jobs = sum(
                1
                for existing in self._jobs.values()
                if existing.status in {JobStatus.QUEUED, JobStatus.RUNNING}
            )
            max_jobs = max(1, int(settings.max_concurrent_jobs))
            if active_jobs >= max_jobs:
                raise RuntimeError(
                    "Another scrape job is already running. "
                    "Please wait for it to finish and retry."
                )
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
                        "tm_country": (job.ticketmaster_country_map.get(artist) or "").upper(),
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
                if settings.headless and settings.disable_engagement_in_headless:
                    logger.warning(
                        "Job %s: skipping engagement phase because HEADLESS=true "
                        "and DISABLE_ENGAGEMENT_IN_HEADLESS=true",
                        job_id,
                    )
                else:
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
                            # Engagement must always run headed (headless=False).
                            # Chrome 115+ headless uses OOP iframes for cross-origin
                            # frames; WebDriver cannot reach the reCAPTCHA bframe
                            # renderer process in that mode. HEADLESS setting only
                            # applies to the Soundcharts phase (see README).
                            er_results = get_engagement_rate_batch(
                                list(ig_map.keys()),
                                chrome_version=settings.chrome_version,
                                headless=False,
                            )
                            for username, er in er_results.items():
                                if er:
                                    ig_map[username]["ig_engagement_rate"] = er
                        except Exception as er_exc:
                            logger.warning(
                                "Job %s: engagement phase failed (results so far preserved): %s",
                                job_id, er_exc,
                            )

            # ── Phase 5: Ticketmaster concerts (undetected Chrome) ──
            if job.include_ticketmaster:
                from .scrapers.ticketmaster import scrape_ticketmaster_concerts
                import time

                job.progress.current_step = "ticketmaster"
                job.updated_at = datetime.now(timezone.utc)

                try:
                    artist_specs = [
                        {
                            "artist_name": e["artist_name"],
                            "country": (e.get("tm_country") or "").upper() or "USA",
                        }
                        for e in collected
                    ]
                    tm_results = scrape_ticketmaster_concerts(
                        artist_specs,
                        chrome_version=settings.chrome_version,
                        proxy_str=settings.tm_proxy or None,
                    )
                    for entry in collected:
                        name = entry["artist_name"]
                        tm_data = tm_results.get(name, {})
                        concerts_raw = tm_data.get("concerts", [])
                        entry["concerts"] = [
                            ConcertData(**c).model_dump() for c in concerts_raw
                        ]
                        entry["tm_profile_url"] = tm_data.get("tm_profile_url", "")
                        entry["first_presale_date"] = tm_data.get("first_presale_date", "")
                        entry["first_onsale_date"] = tm_data.get("first_onsale_date", "")
                except Exception as tm_exc:
                    logger.warning(
                        "Job %s: ticketmaster phase failed (results so far preserved): %s",
                        job_id, tm_exc,
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
