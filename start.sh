#!/usr/bin/env bash
# Sets up (first run only) and launches the Axopar range planner:
# FastAPI backend on :8000, Vite frontend on :2343. See README.md "Quick
# Start" for what this mirrors and MapTiler API key setup for a real basemap.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY_PACKAGES=(packages/fuel-model apps/api services/routing services/marine-data services/isochrone services/diagnostics)

echo "==> Checking Python virtualenvs..."
for d in "${PY_PACKAGES[@]}"; do
  if [ ! -d "$d/.venv" ]; then
    echo "    creating venv + installing $d"
    python3 -m venv "$d/.venv"
    "$d/.venv/bin/pip" install -e "$d" -e "$d[dev]" -q
  fi
done

# apps/api imports the other four packages directly, so it needs them
# installed into ITS OWN venv too (see apps/api/app/main.py).
if [ ! -f "apps/api/.venv/.deps_linked_v2" ]; then
  echo "    linking sibling packages into apps/api's venv"
  apps/api/.venv/bin/pip install -e packages/fuel-model -e services/routing \
    -e services/marine-data -e services/isochrone -e services/diagnostics -q
  touch apps/api/.venv/.deps_linked_v2
fi

echo "==> Checking frontend dependencies..."
if [ ! -d node_modules ]; then
  npm install
fi

# Free the ports if a previous run left something behind.
for port in 8000 2343; do
  pid=$(lsof -ti:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pid" ]; then
    echo "==> Port $port in use, stopping existing process ($pid)"
    kill -9 $pid 2>/dev/null || true
  fi
done

cleanup() {
  echo ""
  echo "==> Shutting down..."
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Starting API (http://localhost:8000)..."
npm run dev:api &

echo "==> Starting web app (http://localhost:2343)..."
npm run dev:web &

echo ""
echo "Open http://localhost:2343 — Ctrl+C here stops both servers."
wait
