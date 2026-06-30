#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/binance-spot-daily-review}"
BRANCH="${BRANCH:-main}"
LOCK_FILE="${LOCK_FILE:-/tmp/binance-spot-daily-review.lock}"

exec 9>"$LOCK_FILE"
flock -n 9

cd "$REPO_DIR"

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/build_cache.py
.venv/bin/python scripts/analyze.py
.venv/bin/python -m pytest -q

git add cache/*.json reports/latest_prefilter.json

if git diff --cached --quiet; then
  echo "No cache changes to commit"
  exit 0
fi

git config user.name "${GIT_COMMITTER_NAME:-binance-vps}"
git config user.email "${GIT_COMMITTER_EMAIL:-binance-vps@users.noreply.github.com}"
git commit -m "chore: update binance spot cache [skip ci]"
git push origin "$BRANCH"
