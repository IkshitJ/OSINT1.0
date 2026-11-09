"""Run with:  python3 -m unittest discover tests"""

import unittest

from server.alert_log import OverLimitLatch


class OverLimitLatchTest(unittest.TestCase):
    def test_raises_at_limit_and_clears_below_95_percent(self):
        latch = OverLimitLatch()
        self.assertFalse(latch.update(49, 50, now=0))
        self.assertTrue(latch.update(50, 50, now=1))
        self.assertTrue(latch.is_over)
        self.assertFalse(latch.update(48, 50, now=2))  # 96 %: still over
        self.assertTrue(latch.update(47, 50, now=3))   # 94 %: cleared
        self.assertFalse(latch.is_over)

    def test_noisy_count_does_not_flap(self):
        # The pattern seen on CAM-03: over, under, over, under every few seconds.
        latch = OverLimitLatch(hold_seconds=10)
        changes = [latch.update(count, 50, now=t) for t, count in
                   [(0, 58), (2, 44), (4, 63), (6, 46), (8, 50), (10, 35)]]
        self.assertEqual(changes, [False] * 6)
        self.assertFalse(latch.is_over)

    def test_sustained_crowd_raises_after_hold_time_then_clears_after_hold_time(self):
        latch = OverLimitLatch(hold_seconds=10)
        self.assertFalse(latch.update(60, 50, now=0))
        self.assertFalse(latch.update(61, 50, now=8))
        self.assertTrue(latch.update(62, 50, now=10))
        self.assertTrue(latch.is_over)

        self.assertFalse(latch.update(30, 50, now=20))
        self.assertFalse(latch.update(55, 50, now=25))  # back over: the wait restarts
        self.assertFalse(latch.update(30, 50, now=26))
        self.assertTrue(latch.update(30, 50, now=36))
        self.assertFalse(latch.is_over)

    def test_no_limit_means_never_over(self):
        latch = OverLimitLatch()
        self.assertFalse(latch.update(1000, 0, now=0))


if __name__ == "__main__":
    unittest.main()
