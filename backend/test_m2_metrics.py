"""Unit tests for M2 metric helpers (no DB required)."""
import unittest
from datetime import date, datetime

from report_metrics import (
    _window_bounds,
    aggregate_blast,
    aggregate_hittrax,
    classify_blast_bat,
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


class BlastBatClassifyTests(unittest.TestCase):
    def test_load_bats(self):
        self.assertEqual(classify_blast_bat("barrel load 32"), "barrel_load")
        self.assertEqual(classify_blast_bat("Handle Load 32"), "handle_load")
        self.assertEqual(classify_blast_bat(None, "under load 32"), "under_load")
        self.assertEqual(classify_blast_bat("BarrelLoad"), "barrel_load")

    def test_game_bat_variants(self):
        self.assertEqual(classify_blast_bat("Victus Game"), "game_bat")
        self.assertEqual(classify_blast_bat("Trea Turner Game Bat"), "game_bat")
        self.assertEqual(classify_blast_bat(""), "game_bat")
        self.assertEqual(classify_blast_bat(None, None), "game_bat")


class AggregateTests(unittest.TestCase):
    def test_blast_empty(self):
        out = aggregate_blast([])
        self.assertEqual(out["swing_count"], 0)
        self.assertIsNone(out["peak_bat_speed"])
        self.assertEqual(out["by_bat"], [])

    def test_blast_values(self):
        rows = [
            {"metric_bat_speed": 60.0, "metric_peak_bat_speed": 65.0, "metric_attack_angle": 10.0},
            {"metric_bat_speed": 62.0, "metric_peak_bat_speed": 66.0, "metric_attack_angle": 12.0},
        ]
        out = aggregate_blast(rows)
        self.assertEqual(out["swing_count"], 2)
        self.assertEqual(out["peak_bat_speed"], 66.0)
        self.assertEqual(out["avg_bat_speed"], 61.0)
        # No equipment tags → all swings count as Game Bat
        self.assertEqual(len(out["by_bat"]), 1)
        self.assertEqual(out["by_bat"][0]["bat_key"], "game_bat")
        self.assertEqual(out["by_bat"][0]["swing_count"], 2)

    def test_blast_by_bat_groups(self):
        rows = [
            {
                "metric_bat_speed": 70.0,
                "metric_peak_bat_speed": 72.0,
                "metric_attack_angle": 8.0,
                "equipment_name": "Victus Game",
            },
            {
                "metric_bat_speed": 60.0,
                "metric_peak_bat_speed": 62.0,
                "metric_attack_angle": 10.0,
                "equipment_name": "barrel load 32",
            },
            {
                "metric_bat_speed": 58.0,
                "metric_peak_bat_speed": 61.0,
                "metric_attack_angle": 11.0,
                "equipment_name": "barrel load 32",
            },
            {
                "metric_bat_speed": 55.0,
                "metric_peak_bat_speed": 57.0,
                "metric_attack_angle": 9.0,
                "equipment_name": "handle load 32",
            },
        ]
        out = aggregate_blast(rows)
        self.assertEqual(out["swing_count"], 4)
        keys = [g["bat_key"] for g in out["by_bat"]]
        self.assertEqual(keys, ["game_bat", "handle_load", "barrel_load"])
        barrel = next(g for g in out["by_bat"] if g["bat_key"] == "barrel_load")
        self.assertEqual(barrel["swing_count"], 2)
        self.assertEqual(barrel["peak_bat_speed"], 62.0)
        self.assertEqual(barrel["avg_bat_speed"], 59.0)

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
        self.assertEqual(out["hard_hit_pct"], 66.7)
        self.assertEqual(out["ideal_la_pct"], 66.7)
        self.assertEqual(out["la_hard_hit_q2"], 10.0)
        self.assertEqual(out["la_hard_hit_q3"], 11.0)

    def test_hittrax_location_breakdown_rhh(self):
        from report_metrics import aggregate_hittrax_location_breakdown

        contacts = [
            # Pull (LF for RHH), inside-ish left zone, mid height
            {
                "ev": 80.0,
                "launch_angle": 10.0,
                "distance": 200.0,
                "horz_angle": -25.0,
                "hand": 1,
                "qd": 4,  # z11 middle
            },
            # Oppo (RF), high
            {
                "ev": 70.0,
                "launch_angle": 30.0,
                "distance": 180.0,
                "horz_angle": 30.0,
                "hand": 1,
                "qd": 1,  # z01 high-middle → high + middle_h
            },
            # Middle field
            {
                "ev": 90.0,
                "launch_angle": 12.0,
                "distance": 250.0,
                "horz_angle": 2.0,
                "hand": 1,
                "qd": 7,  # z21 low-middle
            },
        ]
        out = aggregate_hittrax_location_breakdown(contacts)
        self.assertEqual(out["handedness"], "right")
        self.assertEqual(out["by_field"]["pull"]["avg_ev"], 80.0)
        self.assertEqual(out["by_field"]["oppo"]["avg_ev"], 70.0)
        self.assertEqual(out["by_field"]["middle"]["avg_ev"], 90.0)
        self.assertIsNotNone(out["by_zone"]["high"]["avg_ev"])
        self.assertIsNotNone(out["by_zone"]["middle_h"]["avg_ev"])


if __name__ == "__main__":
    unittest.main()
