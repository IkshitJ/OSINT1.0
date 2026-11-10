# Crowd Monitor: Ujjain

One screen for the crowd-safety control room at the Ujjain Simhastha (Kumbh), and one program behind it.

| Panel | What it shows |
|---|---|
| **Ujjain situation** (map) | The four monitored ghats, each with its live gate count against its limit and its CCTV head count. Live traffic, 2D and 3D satellite views. |
| **Sensor gates** | The load-cell gate simulation of one ghat at a time. A tab per ghat switches between them (so does clicking a ghat on the map). |
| **CCTV head count** | The four ghat cameras, always all on screen, with heads counted live. |
| **Overcrowding log** | Every time a ghat's gates or camera went over the limit or came back under it. |

## Run it

```bash
./run.sh
```

That is all: it opens `http://localhost:3000` when the server is up. The first run sets everything up (a few minutes, mostly downloading PyTorch); after that it starts in about a second.

It needs macOS or Linux with `make` and a C++ compiler (on a Mac: `xcode-select --install`) and either [uv](https://docs.astral.sh/uv/) or Python 3.10+. On Apple Silicon the head detection runs on the GPU (MPS) automatically.

| Address | What |
|---|---|
| `http://localhost:3000/` | The dashboard |
| `http://localhost:3000/sensor/` | The full sensor-gate simulation page (`?ghat=triveni-ghat` picks a ghat): controls, serial monitor, accuracy benchmark |
| `http://localhost:3000/cctv/` | The full CCTV page: single-camera view, tripwire and zone editing, model choice, charts |

`./run.sh --no-open` starts without opening the browser; `PORT=8080 ./run.sh` uses another port.

## The ghats

Four bathing ghats on the Shipra used at the Simhastha. Each one has a marker on the map, a gate simulation and a camera. They are listed in `GHATS` in [server/config.py](server/config.py); add one there and it appears everywhere.

| Ghat | Where the coordinates come from |
|---|---|
| Ram Ghat | OpenStreetMap place "Ram Ghat" |
| Mangalnath Ghat | Mangalnath Mandir, whose steps are the ghat |
| Triveni Ghat | Navagraha (Shani) Mandir at Triveni, on the Indore road |
| Gau Ghat | A geotagged Simhastha 2016 photo of the ghat on Wikimedia Commons |

The positions are each ghat's landmark, good to roughly 100 m; survey them before relying on them.

### Sensor gates

Every ghat runs its own simulator: the real Arduino gate firmware, compiled natively, against a physics model of an entry and an exit corridor (details in [sensor/README.md](sensor/README.md)). All ghats use the same gate layout; they differ in their crowd, their limit and how many people are inside at the start, set per ghat in `server/config.py`. The simulators run whether or not anyone is looking, so every ghat's count stays live on the map. Changes made on a ghat's simulation page (crowd rate, limit, adding a crowd) stay until the server restarts.

### CCTV footage

Each camera plays the video named after its ghat in [footage/](footage/README.md) (`footage/gau-ghat.mp4`, …) or a real stream set with `CCTV_SOURCE_<GHAT>`. A camera without a video says so and offers **Add a video…**, which saves the file into `footage/` under the right name.

Out of the box every camera plays a stand-in clip downloaded by `./run.sh`: fixed-camera crowd scenes from Mixkit and Wikimedia Commons (Shibuya crossing, a Paris station, a Glasgow street and a ghat on Pushkar Lake). They are placeholders for the prototype and do not show the Ujjain ghats; each camera's head count is still reported against its own ghat. Sources and licences are in [footage/README.md](footage/README.md).

## How it works

```
                      ┌─ sensor simulators (one process per ghat) ─► SensorWatch ──┐
one server (:3000) ───┤                                                            ├─► AlertLog ─► /api/events ─► every open dashboard
                      └─ CCTV worker (all cameras, one model call) ─► HeadcounterWatch ┘
```

- **Sensor gates:** the gate firmware decides when a ghat is overcrowded (its `alert` flag; the limit is set on the board). The log records the moment it switches on and off, per ghat.
- **CCTV:** the cameras only count heads, so the server compares each camera's heads-in-view with `HEADCOUNTER_DEFAULT_LIMIT` / `HEADCOUNTER_CAMERA_LIMITS`. Per-frame head counts jump around, so a camera must stay over its limit for `HEADCOUNTER_HOLD_SECONDS` (10 s) to raise an alert, and stay below 95 % of the limit for 10 s to clear it.
- **The log** lives on the server, not in the browser, so every viewer sees the same history and a page reload loses nothing. It is in memory, so it resets when the server restarts.

### What keeps it light

- **One model call for all cameras.** A single thread reads a frame from each camera and runs them through the head detector together, which costs the GPU little more than one camera. Measured on an M4: about half of one CPU core for four cameras at 12 analysed frames a second each.
- **Idle when nobody is watching.** With no dashboard open, cameras are analysed once a second (enough for the overcrowding log) and the server uses a few percent of a core. A hidden browser tab counts as not watching.
- **One connection for all pictures.** The four camera pictures reach the browser over a single WebSocket, which leaves the browser's connections free for the map.
- **The map redraws vehicles only as often as they visibly move:** about twice a second at city zoom, up to 30 times a second zoomed right in.
- **The gate scene redraws only when a new simulation frame arrives.**

## Configuration

All settings are environment variables (see [server/config.py](server/config.py)); any of them can go in `.env`, and real environment variables override the file.

| Variable | Default | Meaning |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `3000` | Where the server listens |
| `CCTV_SOURCE_<GHAT>` | *(file in `footage/`)* | A camera's source: RTSP URL, file path or webcam number, e.g. `CCTV_SOURCE_RAM_GHAT=rtsp://…` |
| `HEADCOUNTER_DEFAULT_LIMIT` | `50` | Heads in view per camera that count as overcrowded |
| `HEADCOUNTER_CAMERA_LIMITS` | *(empty)* | Per-camera overrides, e.g. `cam1=80,cam3=40` (cam1 is the first ghat) |
| `HEADCOUNTER_HOLD_SECONDS` | `10` | How long a camera must stay over (or back under) its limit before the log records it |
| `HEADCOUNTER_POLL_SECONDS` | `2` | How often camera counts are checked against the limits |
| `CCTV_ANALYSIS_FPS` | `12` | Frames analysed per second per camera while someone is watching |
| `CCTV_IDLE_ANALYSIS_FPS` | `1` | The same with nobody watching |
| `TOMTOM_API_KEY` | *(empty)* | Turns on the live traffic layer |
| `TOMTOM_DAILY_TILE_BUDGET` | `6000` | Traffic tiles fetched from TomTom per UTC day; keeps a month inside the 200K free tier |
| `MAP_STYLE_URL` | CARTO Dark Matter | Basemap style |
| `ALERT_LOG_SIZE` | `500` | Events kept in the log |
| `ANTHROPIC_API_KEY` | *(empty)* | Only for the optional crowd-risk forecast (`POST /cctv/api/analyze`) |

Each ghat's gate limit, crowd rate and starting headcount are in `GHATS` in `server/config.py`.

### Live traffic

Live traffic needs a [TomTom](https://developer.tomtom.com/) API key (free tier available). Without one, the **Traffic** button is struck through and clicking it says why.

```bash
cp .env.example .env      # then put your key after TOMTOM_API_KEY=
./run.sh                  # restart to pick it up
```

With a key, every road TomTom covers is colored by how fast traffic is moving compared with free flow: green (85 % or more), amber (55–85 %), red (jam), dark red (closed).

Vehicle dots drive on the roads TomTom covers, in real time at TomTom's live speed for each road (the road type's normal speed × the live speed ratio, as gods-eye-view does), and bunch up where a road is jammed. What is live: which roads are slow and how fast traffic moves on them. What is not: TomTom publishes no vehicle positions or counts (nor does any public source for Ujjain), so each dot is a stand-in placed along a live road, and the `VEH-####` tags are labels for those dots, not real vehicle IDs. Zoomed out, a thinner sample of the vehicles is shown.

**Sim cars** (on by default; click to hide) adds white *simulated* vehicles on the OpenStreetMap roads TomTom has no data for, the way gods-eye-view fills in every road. Each OSM road is matched to the TomTom line it lies on (within 35 m, heading the same way), so roads with live data never get simulated cars. In Ujjain TomTom covers mainly the main roads (about 5 road segments per km² in the centre; zooming in adds none), so most side streets have no live data.

The key stays on the server: the browser fetches traffic tiles from `/api/traffic/flow/{z}/{x}/{y}.pbf`, which the server forwards to TomTom. Tiles are cached for 2 minutes and shared by every viewer, and the map refreshes them every 2 minutes. When TomTom fails or the daily budget is used up, the last copy of each tile is shown instead. The budget counter is in memory, so a server restart resets it. If TomTom rejects the key, the map shows a message saying so.

### Satellite views

**2D Satellite** shows flat satellite imagery (Esri World Imagery), north up. **3D Satellite** adds terrain relief (AWS Terrain Tiles), 3D buildings from OpenStreetMap and a tilted camera. One is active at a time; click the lit button again for the dark map. In both satellite views you can't zoom past the deepest imagery Esri has for the spot you're looking at (zoom 18 imagery in central Ujjain), so the "Map data not yet available" tile never shows; the limit follows you as you pan. Right-drag (or Ctrl-drag) tilts and rotates; the compass button resets. Check Esri's terms before commercial use.

## Testing

```bash
.venv/bin/python -m unittest discover -s tests     # alert rules, ghat settings; no server needed

# Browser check: opens the running dashboard in headless Chromium at six window sizes
# (desktop to phone), checks the map, the ghats' live numbers and that all four cameras
# are completely on screen with a picture, and saves dashboard.png.
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/playwright install chromium              # one-time, ~500 MB in ~/Library/Caches/ms-playwright
.venv/bin/python tests/browser_check.py            # with ./run.sh running
```

## Before production

- **Real cameras and gates:** point each camera at its stream with `CCTV_SOURCE_<GHAT>`. The gates are a simulation; real gate boards would feed `SensorWatch` (server/feeds/sensor.py) the same frames over serial or the network.
- **Basemap licence:** CARTO's free style is for non-commercial use. Use a licensed style or self-host tiles (e.g. Protomaps) via `MAP_STYLE_URL`.
- **HTTPS and buffering:** put the server behind a reverse proxy with HTTPS. Disable response buffering for `/api/events` and `/sensor/api/*/stream` (the server already sends `X-Accel-Buffering: no` for nginx) and allow WebSocket upgrades on `/cctv/ws`.
- **Persist the log:** swap the in-memory `AlertLog` for a database if history must survive restarts or be audited.
- **Access control:** there is no login, and anyone who can reach the server can change a simulation or replace a camera's footage. Keep it on a private network or behind your SSO proxy.

## Layout

```
run.sh               one command: set up on first run, then start the server
requirements.txt     Python packages (requirements-dev.txt: the browser check)
server/              the one web server
  __main__.py        python -m server
  app.py             every address: dashboard API, /sensor API, mounts /cctv and the pages
  config.py          every setting, and the list of ghats
  sensor_sim.py      runs one sensor simulator process per ghat
  feeds/sensor.py    gate frames -> ghat readings + overcrowding log entries
  feeds/headcounter.py  camera counts -> overcrowding log entries
  alert_log.py       the overcrowding log + the over/under-limit rule
  source_status.py   is each source working, and its latest numbers
  event_hub.py       pushes live events to every open browser
  traffic_proxy.py   TomTom traffic tiles: cache, daily budget, key kept server-side
web/                 the dashboard page
  js/main.js         wires the panels together
  js/crowd-map.js    map, ghat markers
  js/sensor-panel.js ghat tabs, live gate numbers, the embedded simulation
  js/cctv-panel.js   the four camera tiles, live pictures, adding footage
  js/traffic-layer.js, satellite-view.js, openfreemap.js   traffic and satellite layers
  js/alert-log.js, live-events.js, header.js
sensor/              the gate firmware, its simulator and simulation page   (sensor/README.md)
cctv/                the CCTV head counter and its full page                (cctv/README.md)
footage/             one video per ghat camera, not in git                  (footage/README.md)
models/              head-detection weights, downloaded on first use, not in git
tests/
```

`sensor/` and `cctv/` were separate projects (`Sensor`, `Headcounter`) that this repository now contains; their own git history stays in those original repositories.
