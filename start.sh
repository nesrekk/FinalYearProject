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

for spec in "mvp_api 8000" "similarity_api 8001" "impact_api 8002"; do
    set -- $spec
    if lsof -nP -iTCP:"$2" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[$1] port $2 is already in use — assuming it's already running, not starting another."
        continue
    fi
    (cd "$ROOT/api" && exec "$PY" -m uvicorn "$1:app" --port "$2" --reload) 2>&1 | sed -l "s/^/[$1] /" &
done

(cd "$ROOT/frontend" && exec npm run dev) 2>&1 | sed -l "s/^/[frontend] /" &

echo "Starting... open the URL the [frontend] line prints (normally http://localhost:5173). Ctrl+C to stop."
wait
