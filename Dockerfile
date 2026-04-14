# muckscraperHeadlinesGoogleNEW/Dockerfile
# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Install system dependencies (Debian/Ubuntu)
RUN apt-get update && apt-get install -y \
    python3-dev \
    libpq-dev \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy the app code
COPY aggregator ./aggregator
COPY news_fetcher ./news_fetcher
COPY boot.sh ./boot.sh

# Expose the port the app runs on
EXPOSE 5000

# Run the app
ENTRYPOINT ["./boot.sh"]