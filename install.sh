#!/usr/bin/env bash
# muckscraperHeadlinesGoogleNEW/install.sh
#
# One-command setup for a fresh clone: creates .env if missing, builds the
# core services, bootstraps the database (pgvector extension + tables +
# admin user via bootstrap_admin.py), then starts the scheduler.
#
# Safe to re-run — each step is idempotent.

set -euo pipefail
cd "$(dirname "$0")"

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1"; }
ok()   { printf '\033[1;32m\xE2\x9C\x93\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "Docker is required: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "The 'docker compose' plugin (Docker Compose v2) is required."

if [ ! -f .env ]; then
    info "No .env found — creating one from .env.sample"
    cp .env.sample .env
    warn "Now edit .env with your API keys, Ollama host, and admin login,"
    warn "then re-run ./install.sh to continue."
    exit 0
fi

# Auto-generate a real SECRET_KEY if it's still the sample placeholder — this
# one is purely mechanical, unlike API keys or the admin password, which need
# a human to actually decide.
if grep -q '^SECRET_KEY=generate_with_python3' .env; then
    info "Generating a real SECRET_KEY"
    if command -v openssl >/dev/null 2>&1; then
        new_key=$(openssl rand -hex 32)
    else
        new_key=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    fi
    sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=${new_key}/" .env && rm -f .env.bak
    ok "SECRET_KEY set"
fi

placeholders_left=false
for pair in \
    "NEWS_API_KEY=your_newsapi_key_here" \
    "GNEWS_API_KEY=your_gnews_key_here" \
    "OLLAMA_HOST=http://your_ollama_ip:11434" \
    "OLLAMA_MODEL=your_model_name_here" \
    "EMBEDDING_MODEL=your_model_name_here" \
    "ADMIN_PASSWORD=replace_with_a_real_admin_password" \
    "MEILI_MASTER_KEY=replace_with_a_real_meilisearch_key"
do
    if grep -q "^${pair}" .env; then
        warn "${pair%%=*} still looks like the .env.sample placeholder"
        placeholders_left=true
    fi
done
if [ "$placeholders_left" = true ]; then
    warn "Some values above are still unedited — the app may not work correctly until you fill them in."
    read -rp "Continue anyway? [y/N] " reply
    case "$reply" in
        [yY]*) ;;
        *) die "Edit .env and re-run ./install.sh" ;;
    esac
fi

info "Building and starting postgres, meilisearch, and app..."
docker compose up -d --build postgres meilisearch app

info "Setting up the database (pgvector extension + tables) and admin user..."
info "(retries automatically while Postgres finishes starting up)"
docker compose exec app python bootstrap_admin.py

info "Starting the scheduler..."
docker compose up -d scheduler

echo
ok "MuckScraper is running."
echo
echo "  Admin app:  http://localhost:5000"
echo "  Log in with the ADMIN_USERNAME / ADMIN_PASSWORD from your .env"
echo
