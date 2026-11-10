#!/usr/bin/env bash
# Runs the whole crowd monitor with one command:   ./run.sh
#
# The first run sets everything up (a few minutes, mostly downloading PyTorch):
#   - a Python environment in .venv with the packages from requirements.txt
#   - the sensor simulator, compiled from sensor/ (needs make and a C++ compiler)
#   - the head-detection model (downloaded by the server on first start)
#   - a stand-in crowd clip for each of the four cameras (see footage/README.md)
# Later runs start in about a second.
#
#   ./run.sh              start, then open http://localhost:3000 in the browser
#   ./run.sh --no-open    start without opening the browser
#   PORT=8080 ./run.sh    use another port
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_VERSION=3.14   # the version this was developed and tested with
PORT="${PORT:-3000}"

say() { printf '\033[1m[crowd-monitor]\033[0m %s\n' "$*"; }

# --- a C++ compiler for the sensor simulator --------------------------------------
if ! command -v make >/dev/null || ! command -v c++ >/dev/null; then
  say "The sensor simulator needs make and a C++ compiler."
  [ "$(uname)" = "Darwin" ] && say "Install Apple's command line tools:  xcode-select --install"
  exit 1
fi

# --- Python environment ---------------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null; then
    say "Creating the Python environment (uv, Python $PYTHON_VERSION)..."
    uv venv .venv --python "$PYTHON_VERSION"
  elif python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    say "Creating the Python environment ($(python3 --version))..."
    python3 -m venv .venv
  else
    say "Python 3.10 or newer is needed. The easiest way to get it is uv:"
    say "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    say "then run ./run.sh again."
    exit 1
  fi
fi

# Install (again) whenever requirements.txt is newer than the last install.
if [ requirements.txt -nt .venv/.installed ]; then
  say "Installing Python packages (the first time this downloads about 500 MB)..."
  if command -v uv >/dev/null; then
    uv pip install --python .venv/bin/python -r requirements.txt
  else
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
  fi
  touch .venv/.installed
fi

# --- stand-in footage for the four cameras ----------------------------------------------
# Fixed-camera crowd clips used as placeholders (sources and licences: footage/README.md).
# A camera that already has a video, of any type, is left alone.
fetch_clip() {  # fetch_clip <ghat-id> <extension> <url>
  ls "footage/$1".* >/dev/null 2>&1 && return 0
  say "Downloading stand-in footage for $1..."
  curl -fsSL --max-time 300 -A "Mozilla/5.0 UjjainCrowdMonitor/1.0 (run.sh)" -o "footage/$1.part" "$3" \
    && mv "footage/$1.part" "footage/$1.$2" \
    || { rm -f "footage/$1.part"; say "Could not download it; the $1 camera will start empty."; }
}
mkdir -p footage
COMMONS=https://upload.wikimedia.org/wikipedia/commons/transcoded
fetch_clip ram-ghat mp4 "https://assets.mixkit.co/videos/4401/4401-720.mp4"
fetch_clip mangalnath-ghat webm "$COMMONS/a/a7/2025-11-06_17-33-16_-_Paris_-_Saint-Lazare.webm/2025-11-06_17-33-16_-_Paris_-_Saint-Lazare.webm.480p.vp9.webm"
fetch_clip triveni-ghat webm "$COMMONS/c/ce/Pedestrians_in_Buchanan_Street%2C_Glasgow.webm/Pedestrians_in_Buchanan_Street%2C_Glasgow.webm.480p.vp9.webm"
fetch_clip gau-ghat mp4 "https://assets.mixkit.co/videos/6799/6799-720.mp4"

# --- start ----------------------------------------------------------------------------------
if [ "${1:-}" != "--no-open" ] && command -v open >/dev/null; then
  # Open the dashboard as soon as the server answers.
  ( for _ in $(seq 1 120); do
      curl -fs "http://localhost:$PORT/healthz" >/dev/null 2>&1 && { open "http://localhost:$PORT"; break; }
      sleep 0.5
    done ) &
fi

say "Starting at http://localhost:$PORT  (Ctrl+C to stop)"
PORT="$PORT" exec .venv/bin/python -m server
