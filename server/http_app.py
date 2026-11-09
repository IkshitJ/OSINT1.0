"""
The dashboard's HTTP server (Python standard library only).

    GET /                the dashboard page (files under web/)
    GET /api/config      settings the page needs: app URLs, map, sites
    GET /api/alerts      the overcrowding log, oldest first
    GET /api/events      Server-Sent Events: a "snapshot" first, then live
                         "alert" and "source" events
    GET /healthz         liveness check for load balancers
"""

from __future__ import annotations

import json
import mimetypes
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from server import config
from server.alert_log import AlertLog
from server.event_hub import EventHub
from server.source_status import SourceStatus

WEB_DIR = (Path(__file__).resolve().parent.parent / "web").resolve()
KEEP_ALIVE_SECONDS = 15


def public_config() -> Dict[str, Any]:
    return {
        "sensorAppUrl": config.SENSOR_PUBLIC_URL,
        "headcounterAppUrl": f"{config.HEADCOUNTER_PUBLIC_URL}/dashboard",
        "map": {
            "styleUrl": config.MAP_STYLE_URL,
            "focusCity": config.FOCUS_CITY,
            "tomtomApiKey": config.TOMTOM_API_KEY or None,
        },
        "sites": config.SITES,
    }


def make_handler(hub: EventHub, alerts: AlertLog, status: SourceStatus):
    class DashboardHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "CrowdMonitor"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            routes = {
                "/api/config": lambda: self._send_json(public_config()),
                "/api/alerts": lambda: self._send_json(alerts.recent()),
                "/api/events": self._stream_events,
                "/healthz": lambda: self._send_json({"ok": True}),
            }
            route = routes.get(path)
            if route:
                route()
            else:
                self._send_static(path)

        def log_message(self, fmt: str, *args: Any) -> None:
            pass  # keep the console for feed messages; put a reverse proxy in front for access logs

        # --- responses -------------------------------------------------------

        def _send(self, code: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: Any) -> None:
            self._send(200, json.dumps(payload).encode(), "application/json")

        def _send_static(self, path: str) -> None:
            relative = "index.html" if path in ("", "/") else path.lstrip("/")
            file = (WEB_DIR / relative).resolve()
            # Refuse anything outside web/ (e.g. "/../server/config.py").
            if WEB_DIR not in file.parents or not file.is_file():
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type.endswith("javascript"):
                content_type += "; charset=utf-8"
            self._send(200, file.read_bytes(), content_type, cache="no-cache")

        def _stream_events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")  # tell nginx not to buffer
            self.end_headers()

            subscription = hub.subscribe()
            try:
                self._write_event("snapshot", {"alerts": alerts.recent(), "sources": status.snapshot()})
                while True:
                    try:
                        name, payload = subscription.get(timeout=KEEP_ALIVE_SECONDS)
                        self._write_event(name, payload)
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # the browser went away
            finally:
                hub.unsubscribe(subscription)
                self.close_connection = True

        def _write_event(self, name: str, payload: Any) -> None:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()

    return DashboardHandler


def make_server(hub: EventHub, alerts: AlertLog, status: SourceStatus) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((config.HOST, config.PORT), make_handler(hub, alerts, status))
    server.daemon_threads = True
    return server
