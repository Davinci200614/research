# Soundcharts Artist Research API

REST API that researches artists via **Soundcharts**, **TrendHero**, and
**OpenAI web search**, returning structured JSON with follower counts, genre,
IG engagement rate, tour links, and venue types.

---

## Architecture

```
app/
├── main.py                ← FastAPI application & endpoints
├── config.py              ← Settings loaded from environment / .env
├── models.py              ← Pydantic request / response schemas
├── jobs.py                ← Thread-based background job manager (4-phase pipeline)
└── scrapers/
    ├── soundcharts.py     ← Soundcharts login, search, follower/genre extraction
    ├── engagement.py      ← TrendHero IG engagement rate + CAPTCHA solving
    └── openai_tools.py    ← OpenAI GPT-4o web search (tour link, venue type)
run.py                     ← Convenience server entry point
Dockerfile                 ← Production Docker image (includes Chrome)
docker-compose.yml         ← One-command deployment
```

## Pipeline Phases

Each job runs a **4-phase pipeline** per artist:

1. **Soundcharts** — Login, search artist, extract TikTok / Spotify /
   Instagram / Bandsintown followers, genre, IG username, profile URL.
2. **Tour link** *(optional)* — Ask OpenAI GPT-4o web search for the
   artist's upcoming tour link.
3. **Venue type** *(optional)* — Ask OpenAI GPT-4o web search for the
   largest venue type the artist has headlined.
4. **Engagement rate** *(optional)* — Visit TrendHero, solve reCAPTCHA
   audio challenge, extract IG engagement rate.

---

## Quick Start (Local)

### 1. Create a virtual environment and install deps

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — set your Soundcharts credentials, OpenAI key, Chrome version
```

Check your Chrome version: open `chrome://version` in Chrome.

### 3. Run the API

```bash
python run.py
```

The server starts on **http://localhost:8000**.
Interactive docs at **http://localhost:8000/docs** (Swagger UI).

---

## Quick Start (Docker)

```bash
docker compose up --build
```

Chrome is installed automatically inside the container. Note that
engagement-rate CAPTCHA solving requires a visible browser; in Docker you
may need Xvfb or skip that phase (`include_engagement: false`).

---

## API Endpoints

| Method   | Path                     | Description                         |
| -------- | ------------------------ | ----------------------------------- |
| `GET`    | `/api/v1/health`         | Health check                        |
| `POST`   | `/api/v1/scrape`         | Start a research job (returns job ID) |
| `GET`    | `/api/v1/jobs`           | List all jobs                       |
| `GET`    | `/api/v1/jobs/{job_id}`  | Get job status / progress / results |
| `DELETE` | `/api/v1/jobs/{job_id}`  | Remove a job from the store         |

### Example: Start a research job

```bash
curl -X POST http://localhost:8000/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "artists": ["Bruno Mars", "Charlie Wilson"],
    "include_engagement": true,
    "include_tour_link": true,
    "include_venue_type": true
  }'
```

Response:

```json
{
  "job_id": "a1b2c3d4",
  "status": "queued",
  "message": "Research job queued for 2 artist(s)"
}
```

### Example: Poll for results

```bash
curl http://localhost:8000/api/v1/jobs/a1b2c3d4
```

Response (while running):

```json
{
  "job_id": "a1b2c3d4",
  "status": "running",
  "progress": {
    "total_artists": 2,
    "completed_artists": 1,
    "current_artist": "Charlie Wilson",
    "current_step": "engagement"
  },
  "result": null,
  "error": null
}
```

Response (when completed):

```json
{
  "job_id": "a1b2c3d4",
  "status": "completed",
  "progress": {
    "total_artists": 2,
    "completed_artists": 2,
    "current_artist": null,
    "current_step": null
  },
  "result": [
    {
      "artist_name": "Bruno Mars",
      "genre": "Pop",
      "tiktok_followers": "50M",
      "spotify_followers": "80M",
      "instagram_followers": "70M",
      "bandsintown_followers": "5M",
      "ig_username": "brunomars",
      "ig_engagement_rate": "2.35%",
      "tour_link": "https://www.brunomars.com/tour",
      "venue_type": "Arena",
      "soundcharts_url": "https://app.soundcharts.com/app/artist/..."
    }
  ],
  "error": null
}
```

---

## Environment Variables

See [`.env.example`](.env.example) for the full list.

| Variable          | Default   | Description                                  |
| ----------------- | --------- | -------------------------------------------- |
| `MAIL_ADDRESS`    | —         | Soundcharts login email                      |
| `MAIL_PASSWORD`   | —         | Soundcharts login password                   |
| `MAIL_ADDRESS1`   | —         | Backup Soundcharts email (rotation)          |
| `MAIL_PASSWORD1`  | —         | Backup Soundcharts password                  |
| `OPENAI_API_KEY`  | —         | OpenAI API key for web search                |
| `CHROME_VERSION`  | `136`     | Must match installed Chrome version          |
| `HEADLESS`        | `false`   | Run Chrome headless (Soundcharts only)       |
| `API_HOST`        | `0.0.0.0` | Server bind address                         |
| `API_PORT`        | `8000`    | Server port                                  |
| `CORS_ORIGINS`    | `*`       | CORS allowed origins (comma-separated)       |

---

## Frontend Integration Notes

1. **CORS** is enabled — the frontend can call the API directly.
   Restrict `CORS_ORIGINS` in production.

2. **Research is slow** (30–60 s per artist, plus engagement-rate phase).
   Use the async job pattern:
   - `POST /api/v1/scrape` → get `job_id`
   - Poll `GET /api/v1/jobs/{job_id}` every few seconds
   - Render results when `status == "completed"`

3. **Job store is in-memory.** Jobs are lost on restart. For persistence,
   swap `JobManager` for Celery + Redis (see `app/jobs.py` docstring).

4. **Browser instances:** Phase 1 (Soundcharts) uses standard Selenium.
   Phase 4 (engagement) uses `undetected-chromedriver` with a visible
   browser for CAPTCHA solving. On a server with limited RAM, avoid
   running too many concurrent jobs.

---

## Original Scripts

The original standalone scripts are preserved for reference:

- `soundchart.py` — CSV-based Soundcharts scraper
- `soundchart_live.py` — Google Sheets-based Soundcharts scraper
- `scrape_engagement.py` — Standalone IG engagement scraper
