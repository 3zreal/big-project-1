#!/bin/bash
# Run the pipeline on a VM (hook this into crontab).
# Adjust PYTHON / env paths for the target machine before scheduling.
set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PYTHON="${PYTHON:-.venv/bin/python}"
"$PYTHON" main.py >> logs/cron.log 2>&1
