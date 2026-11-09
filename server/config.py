"""
All settings for the crowd monitor, read from environment variables.

Every value has a default that works on a single laptop with the Sensor
simulator and the Headcounter app running locally, so `python3 -m server`
works out of the box. In production, set the variables instead of editing code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file(path: Path) -> None:
    """
    Reads KEY=value lines from a .env file into the environment, so secrets
    like TOMTOM_API_KEY live in one file instead of every start command.
    Real environment variables win over the file.
    """
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file(ENV_FILE)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _parse_camera_limits(raw: str) -> Dict[str, int]:
    """Parses "cam1=80,cam3=40" into {"cam1": 80, "cam3": 40}."""
    limits: Dict[str, int] = {}
    for pair in filter(None, (p.strip() for p in raw.split(","))):
        camera_id, _, value = pair.partition("=")
        limits[camera_id.strip()] = int(value)
    return limits


# --- This dashboard ----------------------------------------------------------

HOST = _env("HOST", "127.0.0.1")
PORT = _env_int("PORT", 3000)

# How many overcrowding events the log keeps in memory (oldest dropped first).
ALERT_LOG_SIZE = _env_int("ALERT_LOG_SIZE", 500)


# --- Sensor app (load-cell gates at Ram Ghat) --------------------------------

# Where this server reads the Sensor live feed from.
SENSOR_URL = _env("SENSOR_URL", "http://localhost:8080").rstrip("/")
# Where the browser loads the Sensor app from (differs when behind a proxy).
SENSOR_PUBLIC_URL = _env("SENSOR_PUBLIC_URL", SENSOR_URL).rstrip("/")
SENSOR_SITE_NAME = _env("SENSOR_SITE_NAME", "Ram Ghat")


# --- Headcounter app (CCTV head counting) ------------------------------------

HEADCOUNTER_URL = _env("HEADCOUNTER_URL", "http://localhost:8000").rstrip("/")
HEADCOUNTER_PUBLIC_URL = _env("HEADCOUNTER_PUBLIC_URL", HEADCOUNTER_URL).rstrip("/")
HEADCOUNTER_POLL_SECONDS = _env_float("HEADCOUNTER_POLL_SECONDS", 2.0)

# Headcounter only shows limits in its own page and never reports an alert,
# so this dashboard decides when a camera is overcrowded.
HEADCOUNTER_DEFAULT_LIMIT = _env_int("HEADCOUNTER_DEFAULT_LIMIT", 50)
HEADCOUNTER_CAMERA_LIMITS = _parse_camera_limits(_env("HEADCOUNTER_CAMERA_LIMITS", ""))
# Per-frame head counts jump around; a camera must stay over (or back under)
# its limit this long before the log records a change.
HEADCOUNTER_HOLD_SECONDS = _env_float("HEADCOUNTER_HOLD_SECONDS", 10.0)


# --- Map ---------------------------------------------------------------------

# Dark basemap. CARTO's free style needs a licence for commercial use;
# swap in your own style URL before going live.
MAP_STYLE_URL = _env(
    "MAP_STYLE_URL", "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
)

# Live traffic needs a TomTom API key (free tier available). Without one the
# traffic layer is simply unavailable. The key is sent to the browser, so
# restrict it to your domain in the TomTom developer portal.
TOMTOM_API_KEY = _env("TOMTOM_API_KEY", "")

# Ujjain city centre and the Ram Ghat gates (OpenStreetMap coordinates).
FOCUS_CITY = {"name": "Ujjain", "lat": 23.1885, "lon": 75.7717, "zoom": 13.5}
SITES = [
    {
        "id": "ram-ghat",
        "name": "Ram Ghat gates",
        "source": "sensor",
        "lat": 23.18874,
        "lon": 75.76493,
    },
]
