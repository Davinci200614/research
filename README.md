py# Soundcharts Artist Research API

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
  audio challenge, extract IG engagement rate. If TrendHero fails,
  the scraper automatically falls back to SocialCat's engagement-rate calculator.

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

## Deploy And Share (Render)

This repo includes a ready-to-deploy Render blueprint in `render.yaml`.

1. Push this repository to GitHub.
2. In Render, click **New** → **Blueprint** and select your repo.
3. Render will detect `render.yaml` and create the web service.
4. In Render dashboard, set required secrets:
  - `MAIL_ADDRESS`
  - `MAIL_PASSWORD`
  - `MAIL_ADDRESS1`
  - `MAIL_PASSWORD1`
  - `OPENAI_API_KEY`
5. Deploy and open your public URL:
  - Health check: `https://<your-service>.onrender.com/api/v1/health`
  - Docs: `https://<your-service>.onrender.com/docs`

Notes:
- Container deployments run with `HEADLESS=true`.
- Engagement scraping is automatically skipped in headless mode when
  `DISABLE_ENGAGEMENT_IN_HEADLESS=true` (default).
- Jobs are in-memory only; restarting the service clears old job history.

---

## API Endpoints

| Method   | Path                     | Description                         |
| -------- | ------------------------ | ----------------------------------- |
| `GET`    | `/api/v1/health`         | Health check                        |
| `POST`   | `/api/v1/scrape`         | Start a research job (returns job ID) |
| `GET`    | `/api/v1/jobs`           | List all jobs                       |
| `GET`    | `/api/v1/jobs/{job_id}`  | Get job status / progress / results |
| `POST`   | `/api/v1/jobs/{job_id}/sync-sheet` | Append completed job results to Google Sheet |
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
| `SHEET_ID`        | —         | Google Sheet ID used by sync endpoint        |
| `WORKSHEET_NAME`  | `Sheet1`  | Worksheet/tab name to append rows to         |
| `GOOGLE_SA_JSON`  | —         | Service account credentials: file path, raw JSON, or base64 JSON |

For cloud secrets (Render), paste the JSON object directly as the value for `GOOGLE_SA_JSON` without adding extra outer quotes.
| `CHROME_VERSION`  | `136`     | Must match installed Chrome version          |
| `HEADLESS`        | `false`   | Run Chrome headless (Soundcharts only)       |
| `MAX_CONCURRENT_JOBS` | `1`    | Max active scrape jobs allowed at once       |
| `DISABLE_ENGAGEMENT_IN_HEADLESS` | `true` | Skip engagement phase when HEADLESS is true |
| `REDIS_URL`       | —         | Redis connection URL for shared job state     |
| `JOB_RETENTION_HOURS` | `24`   | How long to keep jobs in Redis                |
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

3. **Job store can be Redis-backed.**
  - Without `REDIS_URL`: jobs are in-memory only (lost on restart, not shared across instances).
  - With `REDIS_URL`: job status/results are shared across instances and retained for `JOB_RETENTION_HOURS`.

4. **Browser instances:** Phase 1 (Soundcharts) uses standard Selenium.
   Phase 4 (engagement) uses `undetected-chromedriver` with a visible
   browser for CAPTCHA solving. On a server with limited RAM, avoid
   running too many concurrent jobs.

