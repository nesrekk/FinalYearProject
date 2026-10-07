#!/usr/bin/env bash
# start.sh — run the whole NBA Hub app from one terminal:
# the three FastAPI backends (8000 / 8001 / 8002) and the Vite frontend.
# Each output line is prefixed with the server it came from.
# Ctrl+C stops everything this script started (and nothing else).
#
# Usage (from anywhere):  ./start.sh
# Needs local Postgres running (see README "Running it locally").

set -u
set -m   # job control: each server runs in its own process group, so it can be stopped as a unit
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3

stop_all() {
    trap - INT TERM
    echo
    echo "Stopping all NBA Hub servers..."
    for pgid in $(jobs -p); do
        kill -TERM -- "-$pgid" 2>/dev/null
    done
    wait 2>/dev/null
    echo "Stopped."
    exit 0
}
trap stop_all INT TERM

# Local Postgres (Homebrew postgresql@18): start it for this boot if it's stopped.
PGBIN=/opt/homebrew/opt/postgresql@18/bin
if ! "$PGBIN/pg_isready" -q 2>/dev/null; then
    echo "[postgres] not running — starting it (brew services run postgresql@18)..."
    brew services run postgresql@18 >/dev/null
    for _ in $(seq 1 20); do
        "$PGBIN/pg_isready" -q 2>/dev/null && break
        sleep 0.5
    done
    if "$PGBIN/pg_isready" -q 2>/dev/null; then
        echo "[postgres] ready."
    else
        echo "[postgres] still not answering — the backends will fail until it is up."
    fi
fi

# Frontend packages: install once if missing (e.g. a fresh checkout or worktree).
if [ ! -d "$ROOT/frontend/node_modules" ]; then
    echo "[frontend] node_modules missing — running npm install..."
    (cd "$ROOT/frontend" && npm install) || { echo "[frontend] npm install failed"; exit 1; }
fi

for spec in "mvp_api 8000" "similarity_api 8001" "impact_api 8002"; do
    set -- $spec
    if lsof -nP -iTCP:"$2" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[$1] port $2 is already in use — assuming it's already running, not starting another."
        continue
    fi
    (cd "$ROOT/api" && exec "$PY" -m uvicorn "$1:app" --port "$2" --reload) 2>&1 | sed -l "s/^/[$1] /" &
done

FRONTEND_LOG="$(mktemp -t nbahub-frontend)"
(cd "$ROOT/frontend" && exec npm run dev) 2>&1 | tee "$FRONTEND_LOG" | sed -l "s/^/[frontend] /" &

echo "Starting... Ctrl+C to stop everything."

# Open the app in the browser once Vite prints its URL (skip with NO_OPEN=1 ./start.sh).
if [ "${NO_OPEN:-0}" != "1" ]; then
    (
        for _ in $(seq 1 60); do
            url=$(grep -oE 'http://localhost:[0-9]+/?' "$FRONTEND_LOG" | head -1)
            if [ -n "$url" ]; then open "$url"; exit 0; fi
            sleep 0.5
        done
    ) &
fi

wait
rm -f "$FRONTEND_LOG"
