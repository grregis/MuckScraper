# news_fetcher/Dockerfile — scheduler / news_fetcher image
# Build from the REPO ROOT (that is the build context):
#   docker build -f news_fetcher/Dockerfile -t muckscraper-scheduler .
# docker-compose already does this via:  context: .  dockerfile: news_fetcher/Dockerfile

FROM python:3.10-slim

WORKDIR /app

# --- Python dependencies -----------------------------------------------------
# Copy ONLY requirements first so this layer stays cached when app code changes.
# requirements.txt lives at the repo root, so with context="." it is at ./requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Playwright + Chromium ---------------------------------------------------
# --with-deps lets Playwright install the exact system libraries it needs,
# so you don't have to hand-maintain the apt-get list (which breaks across
# Debian releases, e.g. libasound2 -> libasound2t64).
RUN playwright install --with-deps chromium

# --- Application code ---------------------------------------------------------
COPY aggregator ./aggregator
COPY news_fetcher ./news_fetcher

# Run the scheduler module (compose overrides this with a sleep+start, but this
# is the correct default so the image is useful on its own).
CMD ["python", "-m", "news_fetcher.scheduler"]
