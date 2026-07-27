#!/bin/sh
set -e

exec gunicorn -b 0.0.0.0:5000 -w "${GUNICORN_WORKERS:-2}" aggregator.app:app
