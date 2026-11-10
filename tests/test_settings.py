"""Settings that decide what each ghat's camera shows, and what the simulation page is told."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import config
from server.sensor_sim import GhatSimulator


class CameraSourceTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(config, "FOOTAGE_DIR", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_footage_means_no_source(self):
        self.assertIsNone(config.camera_source("gau-ghat"))

    def test_uses_the_video_named_after_the_ghat(self):
        (self.folder / "gau-ghat.txt").write_text("not a video")
        (self.folder / "gau-ghat.mp4").write_bytes(b"")
        self.assertEqual(config.camera_source("gau-ghat"), str(self.folder / "gau-ghat.mp4"))

    def test_a_configured_stream_wins_over_the_file(self):
        (self.folder / "gau-ghat.mp4").write_bytes(b"")
        with mock.patch.dict(os.environ, {"CCTV_SOURCE_GAU_GHAT": "rtsp://10.0.0.5/stream"}):
            self.assertEqual(config.camera_source("gau-ghat"), "rtsp://10.0.0.5/stream")

    def test_one_camera_per_ghat_in_order(self):
        cameras = config.cameras()
        self.assertEqual([c["ghat_id"] for c in cameras], [g["id"] for g in config.GHATS])
        self.assertEqual([c["id"] for c in cameras], [f"cam{n}" for n in range(1, len(config.GHATS) + 1)])


class SimulatorSettingsTest(unittest.TestCase):
    def setUp(self):
        ghat = {"id": "ram-ghat", "sensor": {"limit": 300, "people_per_min": 14, "inbound": 0.5}}
        self.simulator = GhatSimulator(Path("/nonexistent"), ghat, on_reading=lambda *_: None)

    def test_starts_from_the_ghats_own_settings(self):
        state = self.simulator.state()
        self.assertEqual((state["limit"], state["rates"]["people_per_min"], state["rates"]["inbound"]), (300, 14, 0.5))

    def test_remembers_what_the_page_changed(self):
        for command in ["rates 60 0.4 3 0.2 0.1", "nonlin 0.6", "inbound 0.85", "speed 4", "serial l450"]:
            self.simulator.send(command)  # no process is running: only the settings change
        state = self.simulator.state()
        self.assertEqual(state["rates"], {"people_per_min": 60, "groups": 0.4, "vehicles_per_min": 3, "stopping": 0.2,
                                          "waiting": 0.1, "nonlinear": 0.6, "inbound": 0.85})
        self.assertEqual((state["speed"], state["limit"]), (4, 450))

    def test_ignores_commands_that_are_not_settings(self):
        before = self.simulator.state()
        for command in ["spawn crowd 500 in", "serial r", "rates oops", "", "serial o120"]:
            self.simulator.send(command)
        self.assertEqual(self.simulator.state(), before)


if __name__ == "__main__":
    unittest.main()
