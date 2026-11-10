"""
The crowd monitor's one web server. Everything runs on one address:

    /                    the dashboard page (files under web/)
    /api/config          settings the page needs: map, ghats, traffic on/off
    /api/alerts          the overcrowding log, oldest first
    /api/events          Server-Sent Events: a "snapshot" first, then live
                         "alert" and "source" events
    /api/traffic/...     live traffic tiles via the TomTom proxy (traffic_proxy.py)
    /healthz             liveness check for load balancers

    /sensor/             the sensor-gate simulation page (?ghat=<id> picks the ghat)
    /sensor/api/...      its API, one simulator per ghat (see below)

    /cctv/               the full CCTV page
    /cctv/api/..., /cctv/ws   its API and live pictures (cctv/api.py)
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, Iterator

import anyio.to_thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from server import config, sensor_sim
from server.alert_log import AlertLog
from server.event_hub import EventHub
from server.feeds.headcounter import HeadcounterWatch
from server.feeds.sensor import SensorWatch
from server.source_status import SourceStatus
from server.traffic_proxy import TrafficProxy

log = logging.getLogger("server")

WEB_DIR = config.REPO_DIR / "web"
SENSOR_WEB_DIR = config.SENSOR_SIMULATION_DIR / "web"
KEEP_ALIVE_SECONDS = 15
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}  # tell nginx not to buffer

# Each open live connection (events, a simulation stream, an MJPEG stream) holds
# a worker thread while it waits; the default pool of 40 is too small for a few tabs.
WORKER_THREADS = 200


class NoCacheStatic(StaticFiles):
    """Static files the browser re-checks on every load, so an edit shows on refresh."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def public_config() -> Dict[str, Any]:
    cameras = {camera["ghat_id"]: camera["id"] for camera in config.cameras()}
    return {
        "sensorAppUrl": "/sensor/",
        "cctvAppUrl": "/cctv/",
        "map": {
            "styleUrl": config.MAP_STYLE_URL,
            "focusCity": config.FOCUS_CITY,
            "traffic": bool(config.TOMTOM_API_KEY),
        },
        "ghats": [
            {"id": g["id"], "name": g["name"], "lat": g["lat"], "lon": g["lon"], "cameraId": cameras[g["id"]]}
            for g in config.GHATS
        ],
    }


def create_app() -> FastAPI:
    hub = EventHub()
    alerts = AlertLog(hub, max_events=config.ALERT_LOG_SIZE)
    status = SourceStatus(hub)
    traffic = TrafficProxy(config.TOMTOM_API_KEY, config.TOMTOM_DAILY_TILE_BUDGET)
    sensor_watch = SensorWatch(config.GHATS, alerts, status)
    simulators = sensor_sim.SensorSimulators(config.SENSOR_SIMULATION_DIR, config.GHATS, sensor_watch.handle_frame)
    running: Dict[str, Any] = {}  # things created at startup: the camera manager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        anyio.to_thread.current_default_thread_limiter().total_tokens = WORKER_THREADS

        log.info("Building the sensor simulator...")
        sensor_sim.build(config.SENSOR_SIMULATION_DIR)
        simulators.start()

        # Imported here, not at the top: it pulls in PyTorch, which takes a few seconds.
        from cctv.manager import CameraManager

        manager = CameraManager(config.cameras())
        manager.start()
        running["cameras"] = manager
        HeadcounterWatch(
            manager.status,
            config.HEADCOUNTER_POLL_SECONDS,
            config.HEADCOUNTER_DEFAULT_LIMIT,
            config.HEADCOUNTER_CAMERA_LIMITS,
            config.HEADCOUNTER_HOLD_SECONDS,
            alerts,
            status,
        ).start()

        log.info("Crowd monitor running at http://%s:%d", config.HOST, config.PORT)
        yield
        simulators.stop()
        manager.stop()

    app = FastAPI(title="Ujjain Crowd Monitor", lifespan=lifespan, docs_url=None, redoc_url=None)

    # --- dashboard -------------------------------------------------------------------

    @app.get("/api/config")
    def get_config() -> Dict[str, Any]:
        return public_config()

    @app.get("/api/alerts")
    def get_alerts():
        return alerts.recent()

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/events")
    def events():
        def stream() -> Iterator[bytes]:
            subscription = hub.subscribe()
            try:
                yield _sse("snapshot", {"alerts": alerts.recent(), "sources": status.snapshot()})
                while True:
                    try:
                        name, payload = subscription.get(timeout=KEEP_ALIVE_SECONDS)
                        yield _sse(name, payload)
                    except queue.Empty:
                        yield b": keep-alive\n\n"
            finally:
                hub.unsubscribe(subscription)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/traffic/status")
    def traffic_status():
        return traffic.status()

    @app.get("/api/traffic/flow/{tile:path}")
    def traffic_tile(request: Request):
        result = traffic.tile(request.url.path)
        if result is None:
            raise HTTPException(status_code=404, detail="Not found")
        code, body, content_type, cache_status = result
        # Freshness is the proxy's job, so the browser must not cache on top of it.
        return Response(body, status_code=code, media_type=content_type,
                        headers={"Cache-Control": "no-store", "X-Traffic-Cache": cache_status})

    # --- sensor gates ------------------------------------------------------------------

    def simulator_or_404(ghat_id: str) -> sensor_sim.GhatSimulator:
        simulator = simulators.get(ghat_id)
        if simulator is None:
            raise HTTPException(status_code=404, detail="Unknown ghat")
        return simulator

    @app.get("/sensor/api/ghats")
    def sensor_ghats():
        keys = ("id", "name", "name_hi", "road", "road_hi", "towards")
        return [{key: ghat.get(key, "") for key in keys} for ghat in config.GHATS]

    @app.get("/sensor/api/{ghat_id}/stream")
    def sensor_stream(ghat_id: str):
        simulator = simulator_or_404(ghat_id)

        def stream() -> Iterator[bytes]:
            viewer = simulator.subscribe()
            try:
                while True:
                    try:
                        yield b"data: " + viewer.get(timeout=5).strip().encode() + b"\n\n"
                    except queue.Empty:
                        yield b": keep-alive\n\n"
            finally:
                simulator.unsubscribe(viewer)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/sensor/api/{ghat_id}/state")
    def sensor_state(ghat_id: str):
        return simulator_or_404(ghat_id).state()

    @app.post("/sensor/api/{ghat_id}/cmd")
    async def sensor_command(ghat_id: str, request: Request):
        simulator = simulator_or_404(ghat_id)
        simulator.send((await request.body()).decode("utf-8", "replace")[:200])
        return {}

    @app.post("/sensor/api/{ghat_id}/firmware")
    async def sensor_firmware(ghat_id: str, request: Request):
        """Reboots that ghat's gate boards with the counter firmware ("v2") or the calibration sketch ("cal")."""
        simulator = simulator_or_404(ghat_id)
        firmware = json.loads(await request.body() or b"{}").get("fw", "v2")
        if firmware not in sensor_sim.FIRMWARES:
            raise HTTPException(status_code=400, detail="unknown firmware")
        await anyio.to_thread.run_sync(simulator.start, firmware)
        return {}

    bench_state: Dict[str, Any] = {"running": False, "done": 0, "total": 0, "result": None, "error": None}
    _load_saved_benchmark(bench_state)

    @app.get("/sensor/api/bench")
    def bench_progress():
        return bench_state

    @app.post("/sensor/api/bench")
    async def bench_start(request: Request):
        if bench_state["running"]:
            raise HTTPException(status_code=409, detail="already running")
        params = json.loads(await request.body() or b"{}")
        threading.Thread(target=_run_benchmark, args=(bench_state, params), daemon=True).start()
        return {}

    # --- CCTV ----------------------------------------------------------------------------

    from cctv.api import create_router  # FastAPI only; PyTorch loads with the manager at startup

    app.include_router(create_router(lambda: running["cameras"]), prefix="/cctv")

    # --- pages (last, so the routes above win) ---------------------------------------------

    app.mount("/sensor", NoCacheStatic(directory=SENSOR_WEB_DIR, html=True), name="sensor-page")
    app.mount("/", NoCacheStatic(directory=WEB_DIR, html=True), name="dashboard")
    return app


def _sse(name: str, payload: Any) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


# --- the sensor firmware's accuracy benchmark (sensor/simulation/bench.py) -----------------

def _bench_module():
    sys.path.insert(0, str(config.SENSOR_SIMULATION_DIR))
    import bench  # noqa: E402  (lives next to the simulator it runs)

    return bench


def _load_saved_benchmark(state: Dict[str, Any]) -> None:
    saved = config.SENSOR_SIMULATION_DIR / "results.json"
    try:
        state["result"] = {"table": json.loads(saved.read_text()), "fws": ["v2"], "seeds": 3,
                           "duration": 600, "source": "results.json"}
    except (OSError, ValueError):
        pass


def _run_benchmark(state: Dict[str, Any], params: Dict[str, Any]) -> None:
    try:
        bench = _bench_module()
        scenarios = [s for s in params.get("scenarios", bench.SCENARIOS) if s in bench.SCENARIOS]
        seeds = max(1, min(10, int(params.get("seeds", 3))))
        duration = max(30, min(3600, float(params.get("duration", 600))))
        state.update(running=True, done=0, total=len(scenarios) * seeds, error=None)

        def progress(done: int, total: int) -> None:
            state["done"] = done

        table = bench.run_suite(["v2"], scenarios, seeds, duration, progress=progress)
        state["result"] = {"table": table, "fws": ["v2"], "seeds": seeds, "duration": duration, "source": "live run"}
    except Exception as error:  # shown on the simulation page
        state["error"] = str(error)
    finally:
        state["running"] = False
