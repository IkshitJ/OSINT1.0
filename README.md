# Crowd Monitor: Ujjain

One screen for the crowd-safety control room. It brings together:

| Panel | What it shows | Comes from |
|---|---|---|
| **Ujjain situation** (map) | Opens on India, then zooms to Ujjain. Ram Ghat marker with live *inside / limit*. Optional live traffic. | Sensor app + TomTom |
| **Sensor gates · Ram Ghat** | The Sensor app (load-cell gates at Ram Ghat), embedded | `~/Downloads/Sensor` |
| **CCTV head count** | The Headcounter app (4 CCTV feeds), embedded | `~/Desktop/Headcounter` |
| **Overcrowding log** | Every time a place went over its limit or came back under it | Both apps |

Pure Python standard library on the server and plain JavaScript modules in the browser. There is no build step and nothing to `pip install`.

## Run it locally

Start the two field apps, then the dashboard:

```bash
# 1. Sensor simulator          -> http://localhost:8080
cd ~/Downloads/Sensor/simulation && python3 server.py

# 2. Headcounter               -> http://localhost:8000
cd ~/Desktop/Headcounter && ./run_dashboard.sh

# 3. This dashboard            -> http://localhost:3000
cd ~/Desktop/OSINT && python3 -m server
```

The order doesn't matter: the dashboard keeps retrying, and the status pills in the top bar turn green as each app comes up.

## Testing

```bash
python3 -m unittest discover tests            # alert rules, no setup needed

# Browser check: opens the running dashboard in headless Chromium, waits for the
# zoom to Ujjain, checks every panel and live feed, saves dashboard.png.
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/playwright install chromium         # one-time, ~500 MB in ~/Library/Caches/ms-playwright
.venv/bin/python tests/browser_check.py
```

## How the overcrowding log works

```
Sensor app ──SSE /api/stream──►┐
                               ├─► server: AlertLog ──SSE /api/events──► every open dashboard
Headcounter ──GET /api/status──┘          (shared, last 500 events)
```

- **Sensor gates:** the gate firmware decides when Ram Ghat is overcrowded (its `alert` flag; limit set on the board, 300 by default). We log the moment it switches on and off.
- **CCTV:** Headcounter has no server-side alert, so the dashboard compares each camera's heads-in-view with `HEADCOUNTER_DEFAULT_LIMIT` / `HEADCOUNTER_CAMERA_LIMITS`. Per-frame head counts jump around, so a camera must stay over its limit for `HEADCOUNTER_HOLD_SECONDS` (10 s) to raise an alert, and stay below 95 % of the limit for 10 s to clear it. Tests: `python3 -m unittest discover tests`.
- Headcounter runs detection only while someone is watching a feed. The embedded CCTV panel does that, but if no dashboard is open, camera counts freeze.

The log lives on the server, not in the browser, so every viewer sees the same history and a page reload loses nothing. It is in memory, so it resets when the server restarts.

## Configuration

All settings are environment variables (see `server/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `3000` | Where the dashboard listens |
| `SENSOR_URL` | `http://localhost:8080` | Where the server reads the Sensor feed |
| `SENSOR_PUBLIC_URL` | = `SENSOR_URL` | Where the *browser* loads the Sensor app |
| `HEADCOUNTER_URL` | `http://localhost:8000` | Where the server polls Headcounter |
| `HEADCOUNTER_PUBLIC_URL` | = `HEADCOUNTER_URL` | Where the *browser* loads Headcounter |
| `HEADCOUNTER_DEFAULT_LIMIT` | `50` | Heads in view per camera that count as overcrowded |
| `HEADCOUNTER_CAMERA_LIMITS` | *(empty)* | Per-camera overrides, e.g. `cam1=80,cam3=40` |
| `HEADCOUNTER_HOLD_SECONDS` | `10` | How long a camera must stay over (or back under) its limit before the log records it; stops noisy counts from flapping |
| `HEADCOUNTER_POLL_SECONDS` | `2` | How often Headcounter is polled |
| `TOMTOM_API_KEY` | *(empty)* | Turns on the live traffic layer |
| `MAP_STYLE_URL` | CARTO Dark Matter | Basemap style |
| `ALERT_LOG_SIZE` | `500` | Events kept in the log |

### Live traffic

Live traffic needs a [TomTom](https://developer.tomtom.com/) API key (free tier available). Without one, the **Traffic** button is struck through and clicking it says why.

```bash
cp .env.example .env      # then put your key after TOMTOM_API_KEY=
python3 -m server         # restart to pick it up
```

Any setting in the table above can go in `.env`; real environment variables override it. If TomTom rejects the key, the map shows a message saying so.

The key is sent to the browser to fetch map tiles, so restrict it to your domain in the TomTom portal. TomTom's coverage of smaller Indian cities can be thin; check Ujjain before relying on it.

## Before production

- **Basemap licence:** CARTO's free style is for non-commercial use. Use a licensed style or self-host tiles (e.g. Protomaps) via `MAP_STYLE_URL`.
- **One origin:** put all three apps behind one reverse proxy (e.g. `/`, `/sensor/`, `/cctv/`) with HTTPS, and set the `*_PUBLIC_URL` variables. Disable response buffering for `/api/events` (the server already sends `X-Accel-Buffering: no` for nginx).
- **Persist the log:** swap the in-memory `AlertLog` for a database if history must survive restarts or be audited.
- **Camera locations:** Headcounter cameras have no coordinates yet. Add them to `SITES` in `server/config.py` (with `source: "headcounter"`) to put them on the map.
- **Access control:** the dashboard has no login; keep it on a private network or behind your SSO proxy.

## Layout

```
server/
  __main__.py        starts everything:  python3 -m server
  config.py          every setting, from environment variables
  http_app.py        HTTP routes: page, /api/config, /api/alerts, /api/events
  alert_log.py       the overcrowding log + the over/under-limit rule
  source_status.py   is each app reachable, and its latest numbers
  event_hub.py       pushes live events to every open browser
  feeds/sensor.py    reads the Sensor live stream
  feeds/headcounter.py  polls Headcounter camera counts
web/
  index.html, styles.css
  js/main.js         wires the panels together
  js/crowd-map.js    map, fly-to-Ujjain, site markers, traffic
  js/alert-log.js    the overcrowding log panel
  js/live-events.js  live connection to the server
  js/header.js       clock and status pills
```
