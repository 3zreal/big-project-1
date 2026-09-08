#!/usr/bin/env bash
# Cron entrypoint: 07:00 and 23:00 ICT. Does not create datasets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron.log"
LOCK="$LOG_DIR/pipeline.lock"

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') ERROR: python not executable: $PYTHON" >>"$LOG"
  exit 1
fi

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') skip: previous run still holds $LOCK" >>"$LOG"
    exit 0
  fi
fi

{
  echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') start"
  set +e
  "$PYTHON" "$ROOT/main.py"
  rc=$?
  echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') done rc=$rc"
  exit "$rc"
} >>"$LOG" 2>&1
