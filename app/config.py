"""
Application configuration via environment variables.

All settings can be overridden with a ``.env`` file in the project root.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Browser ──
    chrome_version: int = 0  # 0 = auto-detect installed Chrome version
    headless: bool = False

    # ── Proxy (Ticketmaster only) ──
    # Format: ip:port:username:password  (authenticated)
    #     or: ip:port                    (open proxy)
    #     or: empty string               (no proxy)
    tm_proxy: str = ""
    proxy_list_file: str = "proxy_list.txt"
    default_max_proxy_tests: int = 40

    # ── Soundcharts credentials ──
    mail_address: str = ""
    mail_password: str = ""

    # ── ShowsOnSale credentials ──
    mail_address1: str = ""
    mail_password1: str = ""

    # ── OpenAI ──
    openai_api_key: str = ""

    # ── Google Sheets (soundchart_live mode) ──
    sheet_id: str = ""
    worksheet_name: str = "Sheet1"
    google_sa_json: str = ""

    # ── API ──
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "*"
    max_concurrent_jobs: int = 1
    disable_engagement_in_headless: bool = True
    redis_url: str = ""
    job_retention_hours: int = 24
    stale_running_job_minutes: int = 20
    ticketmaster_page_load_timeout_seconds: int = 60

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
