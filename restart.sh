#!/usr/bin/env bash
# muckscraperHeadlinesGoogleNEW/restart.sh

docker compose down
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete 2>/dev/null
docker compose up --build
