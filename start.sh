#!/usr/bin/env bash
# Starts BuildAnything: the backend and the app, both on this computer only, then opens it in your browser.
# The first run sets everything up (needs uv, Node.js and npm). Press Ctrl+C to stop both.
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "uv isn't installed. Get it from https://docs.astral.sh/uv/"; exit 1; }
command -v npm >/dev/null || { echo "Node.js isn't installed. Get it from https://nodejs.org/"; exit 1; }

PY=backend/.venv/bin/python
[ -x backend/.venv/Scripts/python.exe ] && PY=backend/.venv/Scripts/python.exe
if [ ! -x "$PY" ]; then
  echo "Setting up the backend, first run only..."
  (cd backend && uv sync)
  PY=backend/.venv/bin/python
  [ -x backend/.venv/Scripts/python.exe ] && PY=backend/.venv/Scripts/python.exe
fi

if [ ! -d app/node_modules ]; then
  echo "Installing the app's packages, first run only..."
  (cd app && npm install)
fi

echo "Building the app..."
(cd app && npx vite build --logLevel warn)

listening() { curl -s -o /dev/null -m 1 "$1"; }
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if listening http://127.0.0.1:8787/api/projects; then
  echo "The backend is already running on port 8787."
else
  (cd backend && exec "../$PY" -m uvicorn buildanything.api:app --host 127.0.0.1 --port 8787) &
  pids+=($!)
fi

if listening http://127.0.0.1:4173/; then
  echo "The app is already running on port 4173."
else
  (cd app && exec npx vite preview --port 4173 --host 127.0.0.1 --strictPort) &
  pids+=($!)
fi

echo "Waiting for the backend..."
for _ in $(seq 1 30); do
  listening http://127.0.0.1:8787/api/projects && break
  sleep 1
done
listening http://127.0.0.1:8787/api/projects || { echo "The backend didn't answer after 30 seconds. See the error above."; exit 1; }

url=http://127.0.0.1:4173
case "$(uname -s)" in
  Darwin) open "$url" ;;
  MINGW*|MSYS*|CYGWIN*) start "" "$url" ;;
  *) xdg-open "$url" >/dev/null 2>&1 || true ;;
esac
echo "BuildAnything is running at $url. Press Ctrl+C to stop it."
if [ ${#pids[@]} -gt 0 ]; then wait; else echo "(Both were already running, so there's nothing to stop here.)"; fi
