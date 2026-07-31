"""
Aggregate Blast / HitTrax metrics for a hitting_assessments row (M2).

For each assessment we take swings on that calendar day only, summarize them,
then (for retests) also summarize previous (stored) and baseline (auto: earliest
initial for the player).
"""
from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _percentile(sorted_vals: list[float], pct: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _safe_stats(vals: list[float]) -> dict[str, Optional[float]]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return {"count": 0, "avg": None, "peak": None, "sd": None, "p90": None}
    peak = max(clean)
    avg = statistics.mean(clean)
    sd = statistics.stdev(clean) if len(clean) > 1 else 0.0
    p90 = _percentile(sorted(clean), 90)
    return {
        "count": len(clean),
        "avg": round(avg, 2),
        "peak": round(peak, 2),
        "sd": round(sd, 2),
        "p90": round(p90, 2) if p90 is not None else None,
    }


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    raise TypeError(f"Unsupported assessment_date type: {type(value)}")


def _window_bounds(row: dict) -> tuple[datetime, datetime]:
    """Assessment calendar day only: [00:00:00, 23:59:59]."""
    d = _as_date(row["assessment_date"])
    return datetime.combine(d, time.min), datetime.combine(d, time(23, 59, 59))


def _fetch_peer(conn, assessment_id: Optional[int]) -> Optional[dict]:
    if not assessment_id:
        return None
    with conn.cursor(dictionary=True) as cur:
        cur.execute(
            """
            SELECT assessment_id, assessment_date, player_name, assessment_type,
                   previous_assessment_id, trainer_name, notes
            FROM hitting_assessments
            WHERE assessment_id = %s
            """,
            (assessment_id,),
        )
        return cur.fetchone()


def _resolve_baseline_row(conn, player_name: str, current_id: Optional[int] = None) -> Optional[dict]:
    """
    Baseline is never trainer-picked: earliest initial for the player,
    else earliest assessment of any type. Skip the current row if passed.
    """
    with conn.cursor(dictionary=True) as cur:
        cur.execute(
            """
            SELECT assessment_id, assessment_date, player_name, assessment_type,
                   previous_assessment_id, trainer_name, notes
            FROM hitting_assessments
            WHERE player_name = %s AND assessment_type = 'initial'
              AND (%s IS NULL OR assessment_id <> %s)
            ORDER BY assessment_date ASC, assessment_id ASC
            LIMIT 1
            """,
            (player_name, current_id, current_id),
        )
        row = cur.fetchone()
        if row:
            return row
        cur.execute(
            """
            SELECT assessment_id, assessment_date, player_name, assessment_type,
                   previous_assessment_id, trainer_name, notes
            FROM hitting_assessments
            WHERE player_name = %s
              AND (%s IS NULL OR assessment_id <> %s)
            ORDER BY assessment_date ASC, assessment_id ASC
            LIMIT 1
            """,
            (player_name, current_id, current_id),
        )
        return cur.fetchone()


def get_comparison_peers(conn, current: dict) -> dict[str, Optional[dict]]:
    """
    previous = stored previous_assessment_id (trainer-confirmed prior).
    baseline = auto earliest initial for this player (not a form field).
    """
    previous = _fetch_peer(conn, current.get("previous_assessment_id"))
    baseline = _resolve_baseline_row(
        conn, current["player_name"], current.get("assessment_id")
    )
    # If "previous" is the same row as baseline, still fine to show once each.
    return {"baseline": baseline, "previous": previous}


def _blast_rows(conn, player_name: str, start: datetime, end: datetime) -> list[dict]:
    """Swing rows from Blast PROD for player in [start, end]."""
    sql = """
        SELECT
            metric_bat_speed,
            metric_peak_bat_speed,
            metric_attack_angle,
            equipment_name,
            equipment_nickname,
            created_date,
            created_time
        FROM blast_swing_metrics_PROD
        WHERE player_name = %s
          AND TIMESTAMP(created_date, COALESCE(created_time, '00:00:00'))
              BETWEEN %s AND %s
    """
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(sql, (player_name, start, end))
            return cur.fetchall()
    except Exception as e:
        logger.warning("Blast query failed: %s", e)
        return []


def _hittrax_rows(conn, player_name: str, start: datetime, end: datetime) -> list[dict]:
    """
    Batted-ball contact rows for a player in [start, end].

    Prefer raw ingest (hittrax_plays + hittrax_session). Use HitTrax **Velo**
    (exit speed) not EBV1 — EBV1 includes negatives/zeros and is not report EV.
    Only rows with Velo > 0 (measured contact). Convert SI → mph / feet.
    Fall back to silver tables (already unit-converted) if the join path fails.
    """
    mps_to_mph = 2.23694
    m_to_ft = 3.28084
    queries = [
        f"""
        SELECT
            p.Velo * {mps_to_mph} AS ev,
            p.Elv AS launch_angle,
            p.Dist * {m_to_ft} AS distance,
            p.TS
        FROM hittrax_plays p
        INNER JOIN hittrax_session s
          ON p.SnId = s.Id AND p.SnUId = s.UId
        WHERE s.UserName = %s
          AND p.TS BETWEEN %s AND %s
          AND p.Velo > 0
        """,
        """
        SELECT Velo AS ev, Elv AS launch_angle, Dist AS distance, TS
        FROM HitTraxSwingSilver
        WHERE UserName = %s AND TS BETWEEN %s AND %s AND Velo > 0
        """,
        """
        SELECT Velo AS ev, Elv AS launch_angle, Dist AS distance, TS
        FROM RBI_HitTraxSwingSilver
        WHERE UserName = %s AND TS BETWEEN %s AND %s AND Velo > 0
        """,
    ]
    for sql in queries:
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, (player_name, start, end))
                rows = cur.fetchall()
                if rows:
                    return rows
        except Exception as e:
            logger.warning("HitTrax query failed: %s", e)
    return []


def aggregate_blast(rows: list[dict]) -> dict[str, Any]:
    """Summarize Blast swings into peak/avg/SD bat speed and attack angle."""
    bat = [float(r["metric_bat_speed"]) for r in rows if r.get("metric_bat_speed") is not None]
    peak_bat = [
        float(r["metric_peak_bat_speed"])
        for r in rows
        if r.get("metric_peak_bat_speed") is not None
    ]
    aa = [float(r["metric_attack_angle"]) for r in rows if r.get("metric_attack_angle") is not None]
    bat_stats = _safe_stats(bat)
    peak_stats = _safe_stats(peak_bat)
    aa_stats = _safe_stats(aa)
    return {
        "swing_count": len(rows),
        "peak_bat_speed": peak_stats["peak"] or bat_stats["peak"],
        "avg_bat_speed": bat_stats["avg"],
        "sd_bat_speed": bat_stats["sd"],
        "avg_attack_angle": aa_stats["avg"],
        "sd_attack_angle": aa_stats["sd"],
    }


def aggregate_hittrax(rows: list[dict]) -> dict[str, Any]:
    """
    Summarize HitTrax contacts (Velo > 0) into EV / LA / distance metrics.

    swing_count here is measured-contact count, not every HitTrax play row.
    """
    evs = [float(r["ev"]) for r in rows if r.get("ev") is not None]
    las = [float(r["launch_angle"]) for r in rows if r.get("launch_angle") is not None]
    dists = [float(r["distance"]) for r in rows if r.get("distance") is not None]
    ev_stats = _safe_stats(evs)

    hard_hit_las: list[float] = []
    ideal_evs: list[float] = []
    peak = ev_stats["peak"]
    if peak:
        # Hard hit ≈ within 10% of session peak EV (common HitTrax-style rule)
        threshold = 0.9 * peak
        for r in rows:
            ev = r.get("ev")
            la = r.get("launch_angle")
            if ev is None:
                continue
            ev_f = float(ev)
            if la is not None and ev_f >= threshold:
                hard_hit_las.append(float(la))
            if la is not None and 5 <= float(la) <= 15:
                ideal_evs.append(ev_f)

    return {
        "swing_count": len(rows),
        "peak_ev": ev_stats["peak"],
        "avg_ev": ev_stats["avg"],
        "p90_ev": ev_stats["p90"],
        "avg_launch_angle": _safe_stats(las)["avg"],
        "avg_la_hard_hit": _safe_stats(hard_hit_las)["avg"],
        "avg_ev_ideal_la": _safe_stats(ideal_evs)["avg"],
        "avg_distance": _safe_stats(dists)["avg"],
    }


def _serialize_side(side: Optional[dict]) -> Optional[dict]:
    """JSON-friendly copy of one assessment metrics block."""
    if side is None:
        return None
    out = dict(side)
    for key in ("start_ts", "end_ts"):
        val = out.get(key)
        if isinstance(val, datetime):
            out[key] = val.strftime("%Y-%m-%d %H:%M:%S")
    if out.get("assessment_date") and hasattr(out["assessment_date"], "strftime"):
        out["assessment_date"] = out["assessment_date"].strftime("%Y-%m-%d")
    return out


def _vald_best(conn, table: str, metric_col: str, player_name: str, start: datetime, end: datetime) -> Optional[float]:
    """Best (MAX) value of a VALD metric for athlete on the assessment day."""
    # Column names have spaces/symbols — quote carefully
    sql = f"""
        SELECT MAX(`{metric_col}`) AS v
        FROM `{table}`
        WHERE athleteName = %s AND recordedEST BETWEEN %s AND %s
    """
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(sql, (player_name, start, end))
            row = cur.fetchone()
            if not row or row.get("v") is None:
                return None
            return round(float(row["v"]), 2)
    except Exception as e:
        logger.warning("VALD query failed %s.%s: %s", table, metric_col, e)
        return None


def _vald_trial_count(conn, table: str, player_name: str, start: datetime, end: datetime) -> int:
    sql = f"""
        SELECT COUNT(*) AS n FROM `{table}`
        WHERE athleteName = %s AND recordedEST BETWEEN %s AND %s
    """
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(sql, (player_name, start, end))
            row = cur.fetchone()
            return int(row["n"]) if row else 0
    except Exception as e:
        logger.warning("VALD count failed %s: %s", table, e)
        return 0


def aggregate_vald(conn, player_name: str, start: datetime, end: datetime) -> dict[str, Any]:
    """
    ForceDecks best-of-day metrics for CMJ / SJ / HJ / IMTP.

    Returns empty-ish zeros when no trials that day (still reportable).
    """
    cmj_n = _vald_trial_count(conn, "VALD_FD_CMJ", player_name, start, end)
    sj_n = _vald_trial_count(conn, "VALD_FD_SJ", player_name, start, end)
    hj_n = _vald_trial_count(conn, "VALD_FD_HJ", player_name, start, end)
    imtp_n = _vald_trial_count(conn, "VALD_FD_IMTP", player_name, start, end)
    return {
        "cmj_trials": cmj_n,
        "cmj_jump_height_ft": _vald_best(
            conn, "VALD_FD_CMJ", "Jump Height (Flight Time)", player_name, start, end
        ),
        "cmj_peak_power_bm": _vald_best(
            conn, "VALD_FD_CMJ", "Peak Power / BM", player_name, start, end
        ),
        "cmj_rsi_mod": _vald_best(
            conn, "VALD_FD_CMJ", "RSI-modified", player_name, start, end
        ),
        "sj_trials": sj_n,
        "sj_jump_height_ft": _vald_best(
            conn, "VALD_FD_SJ", "Jump Height (Flight Time)", player_name, start, end
        ),
        "sj_peak_power_bm": _vald_best(
            conn, "VALD_FD_SJ", "Peak Power / BM", player_name, start, end
        ),
        "hj_trials": hj_n,
        "hj_best_rsi": _vald_best(
            conn, "VALD_FD_HJ", "Best RSI (Jump Height/Contact Time)", player_name, start, end
        ),
        "hj_best_jump_height": _vald_best(
            conn, "VALD_FD_HJ", "Best Jump Height (Flight Time)", player_name, start, end
        ),
        "imtp_trials": imtp_n,
        "imtp_peak_force_bm": _vald_best(
            conn, "VALD_FD_IMTP", "Peak Vertical Force / BM", player_name, start, end
        ),
        "imtp_rfd_100": _vald_best(
            conn, "VALD_FD_IMTP", "RFD - 100ms", player_name, start, end
        ),
    }


def metrics_for_assessment(conn, row: dict) -> dict[str, Any]:
    """Blast + HitTrax + VALD aggregates for a single hitting_assessments row."""
    start, end = _window_bounds(row)
    player = row["player_name"]
    blast_rows = _blast_rows(conn, player, start, end)
    hittrax_rows = _hittrax_rows(conn, player, start, end)
    return {
        "assessment_id": row["assessment_id"],
        "player_name": player,
        "assessment_type": row.get("assessment_type"),
        "assessment_date": row.get("assessment_date"),
        "start_ts": start,
        "end_ts": end,
        "blast": aggregate_blast(blast_rows),
        "hittrax": aggregate_hittrax(hittrax_rows),
        "vald": aggregate_vald(conn, player, start, end),
    }


def build_report_bundle(conn, current: dict) -> dict[str, Any]:
    """
    current / previous / baseline metric blocks for PDF or API.

    previous comes from previous_assessment_id on the row.
    baseline is auto-resolved (earliest initial for the player).
    """
    peers = get_comparison_peers(conn, current)
    return {
        "current": metrics_for_assessment(conn, current),
        "previous": metrics_for_assessment(conn, peers["previous"]) if peers["previous"] else None,
        "baseline": metrics_for_assessment(conn, peers["baseline"]) if peers["baseline"] else None,
    }


def build_report_bundle_json(conn, current: dict) -> dict[str, Any]:
    """Same as build_report_bundle but with datetimes as strings for jsonify."""
    bundle = build_report_bundle(conn, current)
    return {
        "current": _serialize_side(bundle["current"]),
        "previous": _serialize_side(bundle["previous"]),
        "baseline": _serialize_side(bundle["baseline"]),
    }
