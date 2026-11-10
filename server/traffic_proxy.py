"""
Live street traffic: a server-side proxy for TomTom traffic-flow vector tiles.

    GET /api/traffic/flow/{z}/{x}/{y}.pbf   one Mapbox Vector Tile, layer "Traffic flow"
    GET /api/traffic/status                 {"hasKey", "dailyCount", "budget"}

The same method gods-eye-view uses:
  * The TomTom key stays on the server; the browser only talks to this proxy.
  * Tiles are cached for 2 minutes (traffic is "live" at that resolution) and
    every viewer shares the cache, so ten open dashboards cost the same as one.
  * A daily tile budget stops a busy day from running past TomTom's free tier
    (200K tiles a month, roughly 6,000 a day). Cache hits never count.
  * When TomTom fails or the budget is spent, the last copy of the tile is
    served instead (marked STALE), because slightly old traffic beats none.

Each road feature carries `traffic_level` = current speed / free-flow speed
(1 = free flowing, 0 = standstill), `road_type`, and `road_closure` when closed.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional, Tuple

log = logging.getLogger("traffic")

UPSTREAM = "https://api.tomtom.com/traffic/map/4/tile/flow/relative/{z}/{x}/{y}.pbf?key={key}"
TILE_PATH = re.compile(r"^/api/traffic/flow/(\d{1,2})/(\d{1,7})/(\d{1,7})\.pbf$")
MAX_ZOOM = 22
TILE_TTL_SECONDS = 120
MAX_CACHED_TILES = 1000  # flow tiles are a few KB each
MAX_TILE_BYTES = 1_000_000
UPSTREAM_TIMEOUT_SECONDS = 10

# (status, body, content type, cache header value)
Response = Tuple[int, bytes, str, str]


class TrafficProxy:
    def __init__(self, api_key: str, daily_budget: int) -> None:
        self._key = api_key
        self._budget = daily_budget
        self._lock = threading.Lock()
        self._tiles: "OrderedDict[str, Tuple[float, bytes]]" = OrderedDict()
        self._day = ""
        self._count = 0

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def status(self) -> dict:
        with self._lock:
            self._roll_day()
            return {"hasKey": self.has_key, "dailyCount": self._count, "budget": self._budget}

    def tile(self, path: str) -> Optional[Response]:
        """The response for a tile path, or None when the path isn't a tile."""
        match = TILE_PATH.match(path)
        if not match:
            return None
        z, x, y = (int(n) for n in match.groups())
        if z > MAX_ZOOM or x >= 2**z or y >= 2**z:
            return _json(400, "bad_tile")
        if not self._key:
            return _json(503, "no_key")

        key = f"{z}/{x}/{y}"
        with self._lock:
            cached = self._tiles.get(key)
            if cached and time.time() - cached[0] < TILE_TTL_SECONDS:
                return 200, cached[1], "application/x-protobuf", "HIT"
            self._roll_day()
            if self._count >= self._budget:
                if cached:
                    return 200, cached[1], "application/x-protobuf", "STALE-BUDGET"
                return _json(429, "budget")
            self._count += 1  # every upstream attempt is billed, even a failed one

        try:
            body = self._fetch(z, x, y)
        except urllib.error.HTTPError as error:
            log.warning("TomTom flow tile %s failed: HTTP %s", key, error.code)
            if error.code in (401, 403):
                return _json(403, "key_rejected")  # stale tiles would hide a broken key
            return self._stale_or(key, _json(502, "upstream"))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            log.warning("TomTom flow tile %s failed: %s", key, error)
            return self._stale_or(key, _json(504, "upstream"))

        with self._lock:
            self._tiles[key] = (time.time(), body)
            self._tiles.move_to_end(key)
            while len(self._tiles) > MAX_CACHED_TILES:
                self._tiles.popitem(last=False)
        return 200, body, "application/x-protobuf", "MISS"

    def _fetch(self, z: int, x: int, y: int) -> bytes:
        url = UPSTREAM.format(z=z, x=x, y=y, key=self._key)
        request = urllib.request.Request(url, headers={"User-Agent": "CrowdMonitor"})
        with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_TILE_BYTES + 1)
        if len(body) > MAX_TILE_BYTES:
            raise OSError("tile too large")
        return body

    def _stale_or(self, key: str, fallback: Response) -> Response:
        with self._lock:
            cached = self._tiles.get(key)
        if cached:
            return 200, cached[1], "application/x-protobuf", "STALE-ERROR"
        return fallback

    def _roll_day(self) -> None:
        # The budget is per UTC day and lives in memory: a restart resets it.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._count = today, 0


def _json(status: int, error: str) -> Response:
    return status, f'{{"error": "{error}"}}'.encode(), "application/json", "NONE"
