FROM python:3.13-slim

# ── Install Google Chrome + audio deps for CAPTCHA solving ───────────────────
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       wget gnupg2 ffmpeg portaudio19-dev \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
       http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps ──────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ── Runtime defaults ─────────────────────────────────────────────────────────
# Headless is required inside Docker (no display server).
# NOTE: Engagement-rate CAPTCHA solving needs a visible browser;
#       skip that phase in headless containers or use a remote display (Xvfb).
ENV HEADLESS=true
ENV API_HOST=0.0.0.0
ENV API_PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
