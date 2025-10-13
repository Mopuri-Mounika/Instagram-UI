# Use a slim Python base
FROM python:3.12-slim

# Avoid interactive tzdata prompts
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install Chromium + Chromedriver and basic dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    ca-certificates \
    fonts-liberation \
    wget \
    unzip \
    curl \
    # helpful debugging tools
    procps \
    && rm -rf /var/lib/apt/lists/*

# Let Selenium know where Chrome and Chromedriver live
ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER=/usr/bin/chromedriver

# Workdir
WORKDIR /app

# Copy requirements first (better layer caching)
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Copy your scraper
COPY app.py /app/app.py

# Default output path (you can override with env var OUTPUT_FILE)
ENV OUTPUT_FILE=/data/Srija_posts.csv

# Make sure a data directory exists (optionally mount a Render Disk here)
RUN mkdir -p /data

# Start the scraper (for a Worker service). It will run once and exit.
CMD ["python", "-u", "app.py"]
