"""
The sensor-gate simulators: one running simulator process per ghat.

Each process is the real Arduino gate firmware compiled natively (see
sensor/simulation/src/harness.cpp) driving a physics model of that ghat's
entry and exit corridors. It prints one JSON frame every 40 ms and takes
one-line commands on its input ("rates 14 0.25 0 0.1 0", "serial l300", ...).

A ghat's simulator keeps running whether or not anyone is looking at it, so
every ghat's count and overcrowding alarm stay live on the map and in the log.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger("sensor")

FIRMWARES = {"v2", "cal"}  # the counter firmware and the calibration sketch
READING_INTERVAL_SECONDS = 0.5  # how often a frame is read for the map and the log
RESTART_DELAY_SECONDS = 3
ALARM_ON = '"alert":true'

# Starting rates every ghat shares; the per-ghat ones come from its settings.
BASE_RATES = {"groups": 0.25, "vehicles_per_min": 0, "stopping": 0.1, "waiting": 0, "nonlinear": 0.25}


def build(simulation_dir: Path) -> None:
    """Compiles the simulator binaries (a no-op when they are up to date)."""
    result = subprocess.run(["make", "-C", str(simulation_dir)], capture_output=True, text=True)
    if result.returncode != 0:
        log.error("%s%s", result.stdout, result.stderr)
        raise SystemExit(
            "Could not build the sensor simulator. It needs make and a C++ compiler: "
            "on macOS run `xcode-select --install`."
        )


class GhatSimulator:
    """One ghat's simulator process, its settings, and the browsers watching it."""

    def __init__(self, simulation_dir: Path, ghat: Dict[str, Any], on_reading: Callable[[str, Optional[Dict[str, Any]]], None]):
        self.ghat = ghat
        self.ghat_id: str = ghat["id"]
        self.fw: Optional[str] = None
        self._bin_dir = simulation_dir / "bin"
        self._on_reading = on_reading
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._viewers: Set["queue.Queue[str]"] = set()
        self._stopping = False

        settings = ghat["sensor"]
        self.limit: int = settings["limit"]
        self.start_inside: int = settings.get("start_inside", 0)
        self.seed: int = settings.get("seed", 1)
        # What the simulation page shows on its sliders; updated as commands pass through.
        self.rates: Dict[str, float] = dict(
            BASE_RATES, people_per_min=settings["people_per_min"], inbound=settings.get("inbound", 0.5)
        )
        self.speed = 1.0

    # --- the process -------------------------------------------------------------

    def start(self, fw: str = "v2") -> None:
        """(Re)starts the simulator with a fresh world, then applies this ghat's settings."""
        with self._lock:
            self._terminate()
            self._proc = subprocess.Popen(
                [str(self._bin_dir / f"zebra_{fw}"), "--interactive", "--scenario", "idle", "--seed", str(self.seed)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
            )
            self.fw = fw
            threading.Thread(target=self._pump, args=(self._proc,), name=f"sim-{self.ghat_id}", daemon=True).start()
        r = self.rates
        self._write(f"rates {r['people_per_min']} {r['groups']} {r['vehicles_per_min']} {r['stopping']} {r['waiting']}")
        self._write(f"nonlin {r['nonlinear']}")
        self._write(f"inbound {r['inbound']}")
        self._write(f"speed {self.speed}")
        if fw == "v2":
            self._write(f"serial l{self.limit}")
            self._write(f"serial o{self.start_inside}")

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._terminate()

    def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    def send(self, command: str) -> None:
        """A command from the simulation page; rate changes are remembered for the next visitor."""
        command = command.strip()
        self._remember(command)
        self._write(command)

    def _write(self, command: str) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(command + "\n")
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass  # it just died; the pump thread restarts it

    def _remember(self, command: str) -> None:
        parts = command.split()
        try:
            if parts[0] == "rates" and len(parts) >= 6:
                keys = ("people_per_min", "groups", "vehicles_per_min", "stopping", "waiting")
                self.rates.update(zip(keys, map(float, parts[1:6])))
            elif parts[0] == "nonlin":
                self.rates["nonlinear"] = float(parts[1])
            elif parts[0] == "inbound":
                self.rates["inbound"] = float(parts[1])
            elif parts[0] == "vehicles":
                self.rates["vehicles_per_min"] = float(parts[1])
            elif parts[0] == "speed":
                self.speed = float(parts[1])
            elif parts[0] == "serial" and parts[1][:1] in ("l", "L") and parts[1][1:].isdigit():
                self.limit = int(parts[1][1:])
        except (IndexError, ValueError):
            pass  # not a settings command, or malformed: the simulator ignores those too

    def state(self) -> Dict[str, Any]:
        return {"ghat": self.ghat_id, "fw": self.fw, "rates": self.rates, "speed": self.speed, "limit": self.limit}

    # --- frames --------------------------------------------------------------------

    def subscribe(self) -> "queue.Queue[str]":
        viewer: "queue.Queue[str]" = queue.Queue(maxsize=50)
        self._viewers.add(viewer)
        return viewer

    def unsubscribe(self, viewer: "queue.Queue[str]") -> None:
        self._viewers.discard(viewer)

    def _pump(self, proc: subprocess.Popen) -> None:
        last_reading = 0.0
        alarm_text_was = None
        for line in proc.stdout:
            for viewer in list(self._viewers):
                try:
                    viewer.put_nowait(line)
                except queue.Full:  # a slow browser: drop its oldest frame rather than wait
                    try:
                        viewer.get_nowait()
                        viewer.put_nowait(line)
                    except (queue.Empty, queue.Full):
                        pass

            # Frames come 25 times a second and can be large in a dense crowd, so
            # only read one twice a second, or at once when the alarm flips.
            now = time.monotonic()
            alarm_text = ALARM_ON in line
            if alarm_text == alarm_text_was and now - last_reading < READING_INTERVAL_SECONDS:
                continue
            last_reading, alarm_text_was = now, alarm_text
            try:
                self._on_reading(self.ghat_id, json.loads(line))
            except ValueError:
                pass  # a partial line while the process was starting or stopping

        # The process ended. If nobody asked it to, say so and start it again.
        with self._lock:
            unexpected = self._proc is proc and not self._stopping
        if unexpected:
            log.warning("Sensor simulator for %s stopped (exit code %s); restarting", self.ghat_id, proc.poll())
            self._on_reading(self.ghat_id, None)
            time.sleep(RESTART_DELAY_SECONDS)
            if not self._stopping:
                self.start(self.fw or "v2")


class SensorSimulators:
    """Every ghat's simulator, by ghat id."""

    def __init__(self, simulation_dir: Path, ghats: List[Dict[str, Any]], on_reading: Callable[[str, Optional[Dict[str, Any]]], None]):
        self.by_ghat: Dict[str, GhatSimulator] = {
            ghat["id"]: GhatSimulator(simulation_dir, ghat, on_reading) for ghat in ghats
        }

    def start(self) -> None:
        for simulator in self.by_ghat.values():
            simulator.start("v2")

    def stop(self) -> None:
        for simulator in self.by_ghat.values():
            simulator.stop()

    def get(self, ghat_id: str) -> Optional[GhatSimulator]:
        return self.by_ghat.get(ghat_id)
