"""Unit tests for M2 metric helpers (no DB required)."""
import unittest
from datetime import date, datetime

from report_metrics import (
    _window_bounds,
    aggregate_blast,
    aggregate_hittrax,
)


class WindowBoundsTests(unittest.TestCase):
    def test_assessment_day_only(self):
        start, end = _window_bounds({"assessment_date": date(2026, 7, 10)})
        self.assertEqual(start, datetime(2026, 7, 10, 0, 0, 0))
        self.assertEqual(end, datetime(2026, 7, 10, 23, 59, 59))

    def test_string_date(self):
        start, end = _window_bounds({"assessment_date": "2026-07-01"})
        self.assertEqual(start.date(), date(2026, 7, 1))
        self.assertEqual(end.date(), date(2026, 7, 1))


class AggregateTests(unittest.TestCase):
    def test_blast_empty(self):
        out = aggregate_blast([])
        self.assertEqual(out["swing_count"], 0)
        self.assertIsNone(out["peak_bat_speed"])

    def test_blast_values(self):
        rows = [
            {"metric_bat_speed": 60.0, "metric_peak_bat_speed": 65.0, "metric_attack_angle": 10.0},
            {"metric_bat_speed": 62.0, "metric_peak_bat_speed": 66.0, "metric_attack_angle": 12.0},
        ]
        out = aggregate_blast(rows)
        self.assertEqual(out["swing_count"], 2)
        self.assertEqual(out["peak_bat_speed"], 66.0)
        self.assertEqual(out["avg_bat_speed"], 61.0)

    def test_hittrax_hard_hit_and_ideal_la(self):
        rows = [
            {"ev": 100.0, "launch_angle": 8.0, "distance": 300.0},
            {"ev": 95.0, "launch_angle": 12.0, "distance": 280.0},
            {"ev": 70.0, "launch_angle": 30.0, "distance": 200.0},
        ]
        out = aggregate_hittrax(rows)
        self.assertEqual(out["peak_ev"], 100.0)
        self.assertEqual(out["swing_count"], 3)
        # 95 and 100 are hard hits (>= 90); their LA avg = 10
        self.assertEqual(out["avg_la_hard_hit"], 10.0)
        # EV in 5-15 LA: 100 and 95 → avg 97.5
        self.assertEqual(out["avg_ev_ideal_la"], 97.5)


if __name__ == "__main__":
    unittest.main()
