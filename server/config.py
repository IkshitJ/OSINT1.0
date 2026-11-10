"""
All settings for the crowd monitor, read from environment variables.

Every value has a default that works on a single laptop, so `./run.sh` works
out of the box. In production, set the variables (or put them in .env)
instead of editing code.

The ghats being monitored are listed in GHATS below. Each ghat gets a marker
on the map, a sensor-gate simulation and a CCTV camera.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_DIR / ".env"
FOOTAGE_DIR = REPO_DIR / "footage"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")


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


# --- This server ---------------------------------------------------------------

HOST = _env("HOST", "127.0.0.1")
PORT = _env_int("PORT", 3000)

# How many overcrowding events the log keeps in memory (oldest dropped first).
ALERT_LOG_SIZE = _env_int("ALERT_LOG_SIZE", 500)


# --- The ghats -------------------------------------------------------------------

# Bathing ghats on the Shipra used at the Ujjain Simhastha (Kumbh).
#
# Coordinates are the ghat's landmark from open sources, good to roughly 100 m:
#   Ram Ghat         OpenStreetMap place node "Ram Ghat"
#   Mangalnath Ghat  Mangalnath Mandir, whose steps are the ghat
#   Triveni Ghat     Navagraha (Shani) Mandir at Triveni, on the Indore road
#   Gau Ghat         geotagged Simhastha 2016 photo of the ghat (Wikimedia Commons)
# `road` / `towards` label the approach in the gate simulation.
#
# `sensor` is how that ghat's gate simulation starts: the overcrowding limit
# on the gate boards, people arriving per minute, the share of them walking in
# (the rest walk out), and how many people are already inside at the start.
GHATS: List[Dict[str, Any]] = [
    {
        "id": "ram-ghat",
        "name": "Ram Ghat",
        "name_hi": "राम घाट",
        "lat": 23.18874,
        "lon": 75.76493,
        "road": "Ramghat Marg",
        "road_hi": "रामघाट मार्ग",
        "towards": "Dani Gate · Mahakaleshwar",
        "sensor": {"limit": 300, "people_per_min": 14, "inbound": 0.5, "start_inside": 120, "seed": 1},
    },
    {
        "id": "mangalnath-ghat",
        "name": "Mangalnath Ghat",
        "name_hi": "मंगलनाथ घाट",
        "lat": 23.22198,
        "lon": 75.78557,
        "road": "Ankpat Marg",
        "road_hi": "अंकपात मार्ग",
        "towards": "Agar Road",
        "sensor": {"limit": 200, "people_per_min": 10, "inbound": 0.5, "start_inside": 60, "seed": 2},
    },
    {
        "id": "triveni-ghat",
        "name": "Triveni Ghat",
        "name_hi": "त्रिवेणी घाट",
        "lat": 23.12802,
        "lon": 75.79390,
        "road": "Indore Road",
        "road_hi": "इंदौर रोड",
        "towards": "Ujjain city",
        "sensor": {"limit": 250, "people_per_min": 18, "inbound": 0.55, "start_inside": 90, "seed": 3},
    },
    {
        "id": "gau-ghat",
        "name": "Gau Ghat",
        "name_hi": "गऊ घाट",
        "lat": 23.17895,
        "lon": 75.75658,
        "road": "Gau Ghat approach road",
        "road_hi": "",
        "towards": "Ujjain city",
        "sensor": {"limit": 150, "people_per_min": 8, "inbound": 0.5, "start_inside": 40, "seed": 4},
    },
]

# The map frames all the ghats above; this is just what that view is called.
FOCUS_CITY = {"name": "Ujjain"}


# --- Sensor gates (load-cell strips at each ghat's entry and exit) -----------------

SENSOR_SIMULATION_DIR = REPO_DIR / "sensor" / "simulation"


# --- CCTV head counting --------------------------------------------------------------

# How often the cameras' counts are checked against their limits.
HEADCOUNTER_POLL_SECONDS = _env_float("HEADCOUNTER_POLL_SECONDS", 2.0)
# Heads in view that count as overcrowded, for every camera or per camera.
HEADCOUNTER_DEFAULT_LIMIT = _env_int("HEADCOUNTER_DEFAULT_LIMIT", 50)
HEADCOUNTER_CAMERA_LIMITS = _parse_camera_limits(_env("HEADCOUNTER_CAMERA_LIMITS", ""))
# Per-frame head counts jump around; a camera must stay over (or back under)
# its limit this long before the log records a change.
HEADCOUNTER_HOLD_SECONDS = _env_float("HEADCOUNTER_HOLD_SECONDS", 10.0)


def camera_source(ghat_id: str) -> Optional[str]:
    """
    What a ghat's camera shows: the CCTV_SOURCE_<GHAT> setting if there is one
    (an RTSP URL, a file path or a webcam number, e.g.
    CCTV_SOURCE_RAM_GHAT=rtsp://user:pass@10.0.0.5/stream), otherwise the
    video file footage/<ghat-id>.<mp4|mov|webm|...>. None when neither exists.
    """
    configured = _env("CCTV_SOURCE_" + ghat_id.upper().replace("-", "_"), "")
    if configured:
        return configured
    for path in sorted(FOOTAGE_DIR.glob(f"{ghat_id}.*")):
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            return str(path)
    return None


def cameras() -> List[Dict[str, Any]]:
    """One camera per ghat, in the same order as GHATS: cam1 watches the first ghat."""
    return [
        {
            "id": f"cam{number}",
            "name": f"CAM-{number:02d} · {ghat['name'].upper()}",
            "location": ghat["name"],
            "ghat_id": ghat["id"],
            "source": camera_source(ghat["id"]),
            "no_source_hint": f"Add footage/{ghat['id']}.mp4 or upload a video for {ghat['name']}",
        }
        for number, ghat in enumerate(GHATS, start=1)
    ]


# --- Map ---------------------------------------------------------------------------

# Dark basemap. CARTO's free style needs a licence for commercial use;
# swap in your own style URL before going live.
MAP_STYLE_URL = _env(
    "MAP_STYLE_URL", "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
)

# Live traffic needs a TomTom API key (free tier available). Without one the
# traffic layer is simply unavailable. The key stays on this server: the
# browser fetches traffic tiles through /api/traffic/flow/...
TOMTOM_API_KEY = _env("TOMTOM_API_KEY", "")
# Upstream traffic tiles allowed per UTC day. 6,000 x 31 days stays inside
# TomTom's 200K-tile monthly free tier. Cached tiles don't count.
TOMTOM_DAILY_TILE_BUDGET = _env_int("TOMTOM_DAILY_TILE_BUDGET", 6000)
