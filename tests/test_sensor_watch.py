"""The sensor watch: one log entry each time a ghat's gate boards switch their alarm, per ghat."""

import unittest

from server.alert_log import CLEARED, RAISED, AlertLog
from server.event_hub import EventHub
from server.feeds.sensor import SensorWatch
from server.source_status import SourceStatus

GHATS = [{"id": "ram-ghat", "name": "Ram Ghat"}, {"id": "gau-ghat", "name": "Gau Ghat"}]


def frame(inside: int, alert: bool, limit: int = 300) -> dict:
    return {"fw": "v2", "inside": inside, "limit": limit, "alert": alert, "in": inside, "out": 0}


class SensorWatchTest(unittest.TestCase):
    def setUp(self):
        hub = EventHub()
        self.alerts = AlertLog(hub, max_events=50)
        self.status = SourceStatus(hub)
        self.watch = SensorWatch(GHATS, self.alerts, self.status)

    def events(self):
        return [(e["place"], e["state"], e["count"]) for e in self.alerts.recent()]

    def test_logs_once_when_the_alarm_goes_on_and_once_when_it_goes_off(self):
        for inside, alert in [(290, False), (300, True), (305, True), (310, True), (280, False), (270, False)]:
            self.watch.handle_frame("ram-ghat", frame(inside, alert))
        self.assertEqual(self.events(), [("Ram Ghat", RAISED, 300), ("Ram Ghat", CLEARED, 280)])

    def test_each_ghat_has_its_own_alarm(self):
        self.watch.handle_frame("ram-ghat", frame(300, True))
        self.watch.handle_frame("gau-ghat", frame(40, False, limit=150))
        self.watch.handle_frame("gau-ghat", frame(150, True, limit=150))
        self.watch.handle_frame("ram-ghat", frame(301, True))
        self.assertEqual(self.events(), [("Ram Ghat", RAISED, 300), ("Gau Ghat", RAISED, 150)])

    def test_status_carries_every_ghats_reading(self):
        self.watch.handle_frame("ram-ghat", frame(120, False))
        self.watch.handle_frame("gau-ghat", frame(40, False, limit=150))
        sensor = self.status.snapshot()["sensor"]
        self.assertTrue(sensor["online"])
        self.assertEqual(sensor["reading"]["sites"]["ram-ghat"]["inside"], 120)
        self.assertEqual(sensor["reading"]["sites"]["gau-ghat"]["limit"], 150)

    def test_calibration_frames_count_nobody_and_raise_nothing(self):
        self.watch.handle_frame("ram-ghat", {"fw": "cal", "alert": True})
        self.assertEqual(self.events(), [])
        self.assertEqual(self.status.snapshot()["sensor"]["reading"]["sites"]["ram-ghat"], {"mode": "calibration"})

    def test_offline_only_when_every_simulator_has_stopped(self):
        self.watch.handle_frame("ram-ghat", frame(120, False))
        self.watch.handle_frame("gau-ghat", frame(40, False))
        self.watch.handle_frame("ram-ghat", None)
        self.assertTrue(self.status.snapshot()["sensor"]["online"])
        self.watch.handle_frame("gau-ghat", None)
        self.assertFalse(self.status.snapshot()["sensor"]["online"])


if __name__ == "__main__":
    unittest.main()
