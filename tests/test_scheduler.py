"""UTC window, kill pause, wind-down once, interval floor."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services import config, db, scheduler, usage_meter


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


class SchedulerWindowTest(unittest.TestCase):
    def setUp(self):
        db.connect("sqlite:///:memory:")
        usage_meter.reset_runtime()
        config.set_kill(False)
        config.set_armed(False)

    def tearDown(self):
        usage_meter.reset_runtime()
        config.set_kill(False)
        db.close()

    def test_interval_below_floor_rejected(self):
        with self.assertRaises(ValueError):
            scheduler.save({"interval_seconds": 10})

    def test_end_before_start_rejected(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            scheduler.save(
                {
                    "window_start": _iso(now),
                    "window_end": _iso(now - timedelta(minutes=1)),
                }
            )

    def test_kill_skips_run_once(self):
        config.set_kill(True)
        with patch("services.book.run_tick") as tick:
            scheduler.run_once()
            tick.assert_not_called()

    def test_outside_window_skips_run_once(self):
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        scheduler.save(
            {
                "enabled": True,
                "window_start": _iso(start),
                "window_end": _iso(start + timedelta(hours=1)),
            }
        )
        with patch("services.book.run_tick") as tick:
            scheduler.run_once()
            tick.assert_not_called()

    def test_wind_down_cancel_and_flatten_once(self):
        with (
            patch("services.conditional.cancel_armed", return_value=2) as cancel,
            patch(
                "services.alpaca_service.flatten_positions",
                return_value={"status": "demo"},
            ) as flatten,
        ):
            first = scheduler.wind_down()
            second = scheduler.wind_down()
        cancel.assert_called_once()
        flatten.assert_called_once()
        self.assertTrue(first["wound_down"])
        self.assertTrue(second["wound_down"])

    def test_stop_does_not_wind_down(self):
        with (
            patch("services.conditional.cancel_armed") as cancel,
            patch("services.alpaca_service.flatten_positions") as flatten,
        ):
            scheduler.save({"enabled": True})
            out = scheduler.stop()
        cancel.assert_not_called()
        flatten.assert_not_called()
        self.assertFalse(out["enabled"])
        self.assertFalse(out["wound_down"])

    def test_start_requires_windows(self):
        with self.assertRaises(ValueError):
            scheduler.start()


if __name__ == "__main__":
    unittest.main()
