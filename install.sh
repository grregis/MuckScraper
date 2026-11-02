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

first_run=false
if [ ! -f .env ]; then
    info "No .env found — creating one from .env.sample"
    cp .env.sample .env
    chmod 600 .env  # it will hold passwords and API keys
    first_run=true
fi

# Auto-generate SECRET_KEY and MEILI_MASTER_KEY if they're still the sample
# placeholders — these are purely mechanical, unlike API keys or the admin
# password, which need a human to actually decide.
random_hex() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        python3 -c "import secrets; print(secrets.token_hex(32))"
    fi
}
if grep -Eq '^SECRET_KEY=(replace_with_a_generated_secret_key|generate_with_python3)' .env; then
    info "Generating a real SECRET_KEY"
    sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=$(random_hex)/" .env && rm -f .env.bak
    ok "SECRET_KEY set"
fi
# Saved API keys are encrypted with CONFIG_ENCRYPTION_KEY, a Fernet key: 32
# url-safe base64 bytes, the same as Fernet.generate_key() produces.
random_fernet_key() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 32 | tr -d '\n' | tr '+/' '-_'
    else
        python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    fi
}
if grep -Eq '^CONFIG_ENCRYPTION_KEY=(replace_with_a_generated_encryption_key)?$' .env; then
    info "Generating a CONFIG_ENCRYPTION_KEY for saved API keys"
    sed -i.bak "s/^CONFIG_ENCRYPTION_KEY=.*/CONFIG_ENCRYPTION_KEY=$(random_fernet_key)/" .env && rm -f .env.bak
    ok "CONFIG_ENCRYPTION_KEY set (back it up with your .env)"
fi
if grep -Eq '^MEILI_MASTER_KEY=(replace_with_a_generated_meilisearch_key|replace_with_a_real_meilisearch_key)' .env; then
    info "Generating a Meilisearch master key"
    sed -i.bak "s/^MEILI_MASTER_KEY=.*/MEILI_MASTER_KEY=$(random_hex)/" .env && rm -f .env.bak
    ok "MEILI_MASTER_KEY set"
fi

if [ "$first_run" = true ]; then
    warn "Now edit .env with your Ollama host and models and an admin password"
    warn "(news API keys are optional; SECRET_KEY and MEILI_MASTER_KEY are already set),"
    warn "then re-run ./install.sh to continue."
    exit 0
fi

placeholders_left=false
for pair in \
    "OLLAMA_HOST=http://your_ollama_ip:11434" \
    "OLLAMA_MODEL=your_model_name_here" \
    "EMBEDDING_MODEL=your_model_name_here" \
    "ADMIN_PASSWORD=replace_with_a_real_admin_password"
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

# --build: the scheduler image carries its own copy of the code and packages,
# so without it an upgrade leaves the scheduler running the old version.
info "Building and starting the scheduler..."
docker compose up -d --build scheduler

echo
ok "MuckScraper is running."
echo
echo "  Admin app:  http://localhost:5000"
echo "  Log in with the ADMIN_USERNAME / ADMIN_PASSWORD from your .env"
echo
