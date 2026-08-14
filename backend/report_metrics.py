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
                   previous_assessment_id, trainer_name, notes,
                   used_blast, used_hittrax, used_vald
            FROM hitting_assessments
            WHERE assessment_id = %s
            """,
            (assessment_id,),
        )
        return cur.fetchone()


def _resolve_baseline_row(conn, player_name: str) -> Optional[dict]:
    """
    Baseline is never trainer-picked: earliest initial for the player,
    else earliest assessment of any type.
    """
    with conn.cursor(dictionary=True) as cur:
        cur.execute(
            """
            SELECT assessment_id, assessment_date, player_name, assessment_type,
                   previous_assessment_id, trainer_name, notes,
                   used_blast, used_hittrax, used_vald
            FROM hitting_assessments
            WHERE player_name = %s AND assessment_type = 'initial'
            ORDER BY assessment_date ASC, assessment_id ASC
            LIMIT 1
            """,
            (player_name,),
        )
        row = cur.fetchone()
        if row:
            return row
        cur.execute(
            """
            SELECT assessment_id, assessment_date, player_name, assessment_type,
                   previous_assessment_id, trainer_name, notes,
                   used_blast, used_hittrax, used_vald
            FROM hitting_assessments
            WHERE player_name = %s
            ORDER BY assessment_date ASC, assessment_id ASC
            LIMIT 1
            """,
            (player_name,),
        )
        return cur.fetchone()


def get_comparison_peers(conn, current: dict) -> dict[str, Optional[dict]]:
    """
    previous = stored previous_assessment_id (trainer-confirmed prior).
    baseline = auto earliest initial for this player (not a form field).

    An assessment that is itself the baseline has nothing to compare against,
    so it reports no baseline rather than the next-oldest assessment.

    When previous and baseline are the same assessment (typical 1st retest),
    keep only previous so the PDF does not show two identical lines/columns.
    """
    previous = _fetch_peer(conn, current.get("previous_assessment_id"))
    baseline = _resolve_baseline_row(conn, current["player_name"])
    if baseline and baseline["assessment_id"] == current.get("assessment_id"):
        baseline = None
    if (
        previous
        and baseline
        and previous["assessment_id"] == baseline["assessment_id"]
    ):
        baseline = None
    return {"baseline": baseline, "previous": previous}


def retest_number_for(conn, row: dict) -> Optional[int]:
    """
    1-based ordinal among this player's retests (by date, then id).

    Returns None for initial assessments or non-retests.
    """
    if str(row.get("assessment_type") or "").strip().lower() != "retest":
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM hitting_assessments
            WHERE player_name = %s
              AND assessment_type = 'retest'
              AND (
                    assessment_date < %s
                 OR (assessment_date = %s AND assessment_id <= %s)
              )
            """,
            (
                row["player_name"],
                row.get("assessment_date"),
                row.get("assessment_date"),
                row["assessment_id"],
            ),
        )
        count = cur.fetchone()[0]
    return int(count) if count else 1


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

    Plate-crossing location for zone charts: **PP1/PP2** (meters) and **QD**
    (HitTrax 1–13 zone id). PBH/PBV are pitch *break*, not plate position.
    Depth of contact: **Intersect3 − PP3** (meters; PP3 ≈ plate depth / front-edge zero).
    Fall back to silver tables (already unit-converted; no PP/QD) if the join path fails.
    """
    mps_to_mph = 2.23694
    m_to_ft = 3.28084
    queries = [
        f"""
        SELECT
            p.Velo * {mps_to_mph} AS ev,
            p.Elv AS launch_angle,
            p.Dist * {m_to_ft} AS distance,
            p.HorzAngle AS horz_angle,
            p.Hand AS hand,
            p.PBH AS pbh,
            p.PBV AS pbv,
            p.PP1 AS pp1,
            p.PP2 AS pp2,
            p.PP3 AS pp3,
            p.QD AS qd,
            p.Intersect1 AS intersect1,
            p.Intersect2 AS intersect2,
            p.Intersect3 AS intersect3,
            p.TS
        FROM hittrax_plays p
        INNER JOIN hittrax_session s
          ON p.SnId = s.Id AND p.SnUId = s.UId
        WHERE s.UserName = %s
          AND p.TS BETWEEN %s AND %s
          AND p.Velo > 0
        """,
        """
        SELECT
            Velo AS ev, Elv AS launch_angle, Dist AS distance,
            NULL AS horz_angle, NULL AS hand, NULL AS pbh, NULL AS pbv,
            NULL AS pp1, NULL AS pp2, NULL AS pp3, NULL AS qd,
            NULL AS intersect1, NULL AS intersect2, NULL AS intersect3, TS
        FROM HitTraxSwingSilver
        WHERE UserName = %s AND TS BETWEEN %s AND %s AND Velo > 0
        """,
        """
        SELECT
            Velo AS ev, Elv AS launch_angle, Dist AS distance,
            NULL AS horz_angle, NULL AS hand, NULL AS pbh, NULL AS pbv,
            NULL AS pp1, NULL AS pp2, NULL AS pp3, NULL AS qd,
            NULL AS intersect1, NULL AS intersect2, NULL AS intersect3, TS
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


def hittrax_contacts_for_charts(rows: list[dict]) -> list[dict[str, Any]]:
    """Slim contact points for PDF charts (JSON-serializable floats / None)."""
    contacts: list[dict[str, Any]] = []
    for r in rows:
        def _f(key: str) -> Optional[float]:
            v = r.get(key)
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        qd_raw = r.get("qd")
        qd: Optional[int] = None
        if qd_raw is not None:
            try:
                qd = int(qd_raw)
            except (TypeError, ValueError):
                qd = None

        contacts.append(
            {
                "ev": _f("ev"),
                "launch_angle": _f("launch_angle"),
                "distance": _f("distance"),
                "horz_angle": _f("horz_angle"),
                "hand": r.get("hand"),
                "pbh": _f("pbh"),
                "pbv": _f("pbv"),
                "pp1": _f("pp1"),
                "pp2": _f("pp2"),
                "pp3": _f("pp3"),
                "qd": qd,
                "intersect1": _f("intersect1"),
                "intersect2": _f("intersect2"),
                "intersect3": _f("intersect3"),
            }
        )
    return contacts


# Assessment Blast bat groups (substring match on equipment_name / nickname).
# Order is PDF display order: game bat first, then load bats.
BLAST_BAT_ORDER: list[str] = [
    "game_bat",
    "handle_load",
    "barrel_load",
    "under_load",
]
BLAST_BAT_LABELS: dict[str, str] = {
    "game_bat": "Game Bat",
    "handle_load": "Handle Load",
    "barrel_load": "Barrel Load",
    "under_load": "Under Load",
}
# (key, label, needles) — needles matched case-insensitively against name+nickname
BLAST_BAT_GROUP_DEFS: list[tuple[str, str, tuple[str, ...]]] = [
    ("barrel_load", "Barrel Load", ("barrel load", "barrelload", "barrel-load")),
    ("handle_load", "Handle Load", ("handle load", "handleload", "handle-load")),
    ("under_load", "Under Load", ("under load", "underload", "under-load")),
]


def classify_blast_bat(
    equipment_name: Optional[str] = None,
    equipment_nickname: Optional[str] = None,
) -> str:
    """
    Map Blast equipment strings to assessment bat groups.

    HEAT sessions use barrel / handle / under load bats plus a game bat.
    Names vary (e.g. \"barrel load 32\"); game-bat labels vary the most, so
    anything that is not an explicit load bat is treated as Game Bat.
    """
    text = f"{equipment_name or ''} {equipment_nickname or ''}".strip().lower()
    text = " ".join(text.split())
    for key, _label, needles in BLAST_BAT_GROUP_DEFS:
        if any(n in text for n in needles):
            return key
    return "game_bat"


def blast_bat_label(bat_key: str) -> str:
    return BLAST_BAT_LABELS.get(bat_key, bat_key.replace("_", " ").title())


def _blast_stats(rows: list[dict]) -> dict[str, Any]:
    """Peak/avg/SD bat speed and attack angle for a swing list."""
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


def aggregate_blast(rows: list[dict]) -> dict[str, Any]:
    """
    Summarize Blast swings overall and by bat group.

    Top-level keys stay day-wide (for snapshot / backward compatibility).
    ``by_bat`` is an ordered list of per-group stats for the PDF.
    """
    overall = _blast_stats(rows)
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        key = classify_blast_bat(r.get("equipment_name"), r.get("equipment_nickname"))
        grouped.setdefault(key, []).append(r)

    by_bat: list[dict[str, Any]] = []
    for key in BLAST_BAT_ORDER:
        group_rows = grouped.get(key) or []
        if not group_rows:
            continue
        stats = _blast_stats(group_rows)
        stats["bat_key"] = key
        stats["bat_label"] = blast_bat_label(key)
        by_bat.append(stats)

    overall["by_bat"] = by_bat
    return overall


def blast_group_stats(blast: Optional[dict], bat_key: str) -> dict[str, Any]:
    """Look up one bat group's stats from an aggregate_blast result."""
    if not blast:
        return {}
    for item in blast.get("by_bat") or []:
        if item.get("bat_key") == bat_key:
            return item
    return {}


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


# HitTrax location breakdown (example-PDF "By Field" / "By Zone" table).
# HorzAngle: 0° ≈ CF; negative ≈ LF; positive ≈ RF (matches spray chart).
HITTRAX_FIELD_ORDER: list[tuple[str, str]] = [
    ("pull", "Pull Side"),
    ("middle", "Middle"),
    ("oppo", "Oppo"),
]
HITTRAX_ZONE_ORDER: list[tuple[str, str]] = [
    ("inside", "Inside"),
    ("middle_h", "Middle"),
    ("outside", "Outside"),
    ("high", "High"),
    ("middle_v", "Middle Height"),
    ("low", "Low"),
]
_FIELD_ANGLE_MID = 15.0  # |HorzAngle| ≤ 15° counts as middle of the field

# Catcher's-view zone ids → left / middle / right (x) and high / mid / low (y).
_ZONE_CATCHER_LEFT = {"z00", "z10", "z20", "outer_tl", "outer_bl"}
_ZONE_CATCHER_MID_H = {"z01", "z11", "z21"}
_ZONE_CATCHER_RIGHT = {"z02", "z12", "z22", "outer_tr", "outer_br"}
_ZONE_HIGH = {"z00", "z01", "z02", "outer_tl", "outer_tr"}
_ZONE_MID_V = {"z10", "z11", "z12"}
_ZONE_LOW = {"z20", "z21", "z22", "outer_bl", "outer_br"}


def _hittrax_hand_is_left(hand: Any) -> bool:
    """HitTrax Hand: 2 / L / left → LHH; 0/1 / R / right (and unknown) → RHH."""
    if hand is None:
        return False
    text = str(hand).strip().lower()
    return text in {"2", "l", "left", "lh", "lhh"}


def _modal_hand_is_left(contacts: list[dict[str, Any]]) -> bool:
    votes = [c.get("hand") for c in contacts if c.get("hand") not in (None, "")]
    if not votes:
        return False
    # Majority vote; ties default to right-handed.
    left = sum(1 for h in votes if _hittrax_hand_is_left(h))
    return left > len(votes) - left


def _field_bucket(horz_angle: Optional[float], hand_is_left: bool) -> Optional[str]:
    if horz_angle is None:
        return None
    try:
        ha = float(horz_angle)
    except (TypeError, ValueError):
        return None
    if abs(ha) <= _FIELD_ANGLE_MID:
        return "middle"
    toward_rf = ha > 0
    if hand_is_left:
        return "pull" if toward_rf else "oppo"
    return "pull" if not toward_rf else "oppo"


def _zone_buckets(zone_id: Optional[str], hand_is_left: bool) -> list[str]:
    """
    Return horizontal and/or vertical zone keys for one contact.

    Inside/outside flip with handedness (catcher-left = inside for RHH).
    """
    if not zone_id:
        return []
    out: list[str] = []
    if zone_id in _ZONE_CATCHER_MID_H:
        out.append("middle_h")
    elif zone_id in _ZONE_CATCHER_LEFT:
        out.append("outside" if hand_is_left else "inside")
    elif zone_id in _ZONE_CATCHER_RIGHT:
        out.append("inside" if hand_is_left else "outside")

    if zone_id in _ZONE_HIGH:
        out.append("high")
    elif zone_id in _ZONE_MID_V:
        out.append("middle_v")
    elif zone_id in _ZONE_LOW:
        out.append("low")
    return out


def _avg_triple(rows: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    evs = [float(r["ev"]) for r in rows if r.get("ev") is not None]
    las = [float(r["launch_angle"]) for r in rows if r.get("launch_angle") is not None]
    dists = [float(r["distance"]) for r in rows if r.get("distance") is not None]
    return {
        "avg_ev": _safe_stats(evs)["avg"],
        "avg_launch_angle": _safe_stats(las)["avg"],
        "avg_distance": _safe_stats(dists)["avg"],
        "contact_count": len(rows),
    }


def aggregate_hittrax_location_breakdown(
    contacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Avg EV / LA / distance by field third and by plate zone.

    Used for the example-PDF location comparison table.
    """
    # Lazy import avoids pulling matplotlib when only metrics are needed.
    from report_charts import _assign_zone_from_contact

    hand_is_left = _modal_hand_is_left(contacts)
    field_buckets: dict[str, list[dict]] = {k: [] for k, _ in HITTRAX_FIELD_ORDER}
    zone_buckets: dict[str, list[dict]] = {k: [] for k, _ in HITTRAX_ZONE_ORDER}

    for c in contacts:
        fb = _field_bucket(c.get("horz_angle"), hand_is_left)
        if fb in field_buckets:
            field_buckets[fb].append(c)
        zid = _assign_zone_from_contact(c)
        for zb in _zone_buckets(zid, hand_is_left):
            if zb in zone_buckets:
                zone_buckets[zb].append(c)

    return {
        "handedness": "left" if hand_is_left else "right",
        "by_field": {k: _avg_triple(v) for k, v in field_buckets.items()},
        "by_zone": {k: _avg_triple(v) for k, v in zone_buckets.items()},
    }


def hittrax_breakdown_value(
    breakdown: Optional[dict],
    section: str,
    key: str,
    field: str,
) -> Optional[float]:
    """Look up one avg metric from a location breakdown dict."""
    if not breakdown:
        return None
    block = (breakdown.get(section) or {}).get(key) or {}
    val = block.get(field)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _serialize_side(side: Optional[dict]) -> Optional[dict]:
    """JSON-friendly copy of one assessment metrics block."""
    if side is None:
        return None
    out = dict(side)
    # Keep metrics API payloads small — chart points stay PDF-only
    out.pop("hittrax_contacts", None)
    out.pop("vald_series", None)
    for key in ("start_ts", "end_ts"):
        val = out.get(key)
        if isinstance(val, datetime):
            out[key] = val.strftime("%Y-%m-%d %H:%M:%S")
    if out.get("assessment_date") and hasattr(out["assessment_date"], "strftime"):
        out["assessment_date"] = out["assessment_date"].strftime("%Y-%m-%d")
    return out


def _vald_daily_series(
    conn,
    table: str,
    metric_cols: list[str],
    player_name: str,
    as_of: date,
) -> list[dict[str, Any]]:
    """
    One row per calendar day (MAX of each metric) through as_of inclusive.
    Missing columns are skipped quietly.
    """
    if not metric_cols:
        return []
    # Probe which columns exist
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(f"SHOW COLUMNS FROM `{table}`")
            existing = {r["Field"] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("VALD column probe failed %s: %s", table, e)
        return []

    cols = [c for c in metric_cols if c in existing]
    if not cols:
        return []

    select_parts = ["DATE(recordedEST) AS session_date"] + [
        f"MAX(`{c}`) AS `{c}`" for c in cols
    ]
    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM `{table}`
        WHERE athleteName = %s
          AND recordedEST <= %s
        GROUP BY DATE(recordedEST)
        ORDER BY session_date ASC
    """
    end_ts = datetime.combine(as_of, time(23, 59, 59))
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(sql, (player_name, end_ts))
            rows = cur.fetchall() or []
    except Exception as e:
        logger.warning("VALD series query failed %s: %s", table, e)
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        day = r.get("session_date")
        if hasattr(day, "strftime"):
            day_s = day.strftime("%Y-%m-%d")
        else:
            day_s = str(day)[:10] if day else None
        point: dict[str, Any] = {"date": day_s}
        for c in cols:
            v = r.get(c)
            if v is None:
                point[c] = None
            else:
                try:
                    point[c] = float(v)
                except (TypeError, ValueError):
                    point[c] = None
        out.append(point)
    return out


def vald_series_for_charts(conn, player_name: str, assessment_date: Any) -> dict[str, Any]:
    """
    Historical daily bests + limb columns for Looker-style VALD PDF cards.
    Window: all trials for the athlete through the assessment date.
    """
    as_of = _as_date(assessment_date)
    return {
        "imtp": _vald_daily_series(
            conn,
            "VALD_FD_IMTP",
            [
                "Peak Vertical Force / BM",
                "Peak Vertical Force",
                "Peak Vertical Force (Left)",
                "Peak Vertical Force (Right)",
                "Peak Vertical Force Asym (%)",
                "RFD - 100ms",
                "RFD - 100ms (Left)",
                "RFD - 100ms (Right)",
                "RFD - 100ms Asym (%)",
                "RFD - 150ms",
                "RFD - 150ms (Left)",
                "RFD - 150ms (Right)",
                "RFD - 150ms Asym (%)",
            ],
            player_name,
            as_of,
        ),
        "hj": _vald_daily_series(
            conn,
            "VALD_FD_HJ",
            [
                "Best RSI (Jump Height/Contact Time)",
                "Mean RSI (Jump Height/Contact Time)",
                "Best Jump Height (Flight Time)",
                "Best Peak Force",
                "Best Peak Force (Left)",
                "Best Peak Force (Right)",
                "Best Peak Force (Asym)",
            ],
            player_name,
            as_of,
        ),
        "cmj": _vald_daily_series(
            conn,
            "VALD_FD_CMJ",
            [
                "Jump Height (Flight Time)",
                "RSI-modified",
                "Peak Power / BM",
                "Eccentric Braking RFD",
                "Eccentric Braking RFD (Left)",
                "Eccentric Braking RFD (Right)",
                "Eccentric Braking RFD Asym (%)",
                "Concentric Duration",
            ],
            player_name,
            as_of,
        ),
        "sj": _vald_daily_series(
            conn,
            "VALD_FD_SJ",
            [
                "Jump Height (Flight Time)",
                "Peak Power / BM",
                "Concentric RFD",
                "Concentric RFD (Left)",
                "Concentric RFD (Right)",
                "Concentric RFD Asym (%)",
            ],
            player_name,
            as_of,
        ),
    }


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


# Display label → VALD dictionary metric_name (from vald_dictionary / VALD API).
VALD_METRIC_DEF_KEYS: list[tuple[str, str]] = [
    ("CMJ Jump Height (FT)", "Jump Height (Flight Time)"),
    ("CMJ Peak Power / BM", "Peak Power / BM"),
    ("CMJ RSI-modified", "RSI-modified"),
    ("SJ Jump Height (FT)", "Jump Height (Flight Time)"),
    ("SJ Peak Power / BM", "Peak Power / BM"),
    ("HJ Best RSI", "Best RSI (Jump Height/Contact Time)"),
    ("HJ Best Jump Height", "Best Jump Height (Flight Time)"),
    ("IMTP Peak Force / BM", "Peak Vertical Force / BM"),
    ("IMTP RFD 100ms", "RFD - 100ms"),
]

# Coach-facing units for VALD comparison-table labels (ForceDecks SI).
VALD_METRIC_UNITS: dict[str, str] = {
    "CMJ Jump Height (FT)": "cm",
    "CMJ Peak Power / BM": "W/kg",
    "CMJ RSI-modified": "m/s",
    "SJ Jump Height (FT)": "cm",
    "SJ Peak Power / BM": "W/kg",
    "HJ Best RSI": "m/s",
    "HJ Best Jump Height": "cm",
    "IMTP Peak Force / BM": "N/kg",
    "IMTP RFD 100ms": "N/s",
}

# Short coach-facing fallbacks when vald_dictionary is unavailable.
VALD_METRIC_DEF_FALLBACKS: dict[str, str] = {
    "Jump Height (Flight Time)": (
        "How high the athlete jumped, estimated from time in the air."
    ),
    "Peak Power / BM": (
        "Peak mechanical power produced in the jump, scaled to body mass."
    ),
    "RSI-modified": (
        "Jump height relative to time on the ground in the countermovement — "
        "a quick indicator of reactive strength."
    ),
    "Best RSI (Jump Height/Contact Time)": (
        "Hop reactivity: jump height divided by ground contact time. "
        "Higher usually means faster elastic rebound."
    ),
    "Best Jump Height (Flight Time)": (
        "Best hop jump height in the set, estimated from flight time."
    ),
    "Peak Vertical Force / BM": (
        "Maximum isometric pulling force relative to body mass — "
        "a strength capacity marker."
    ),
    "RFD - 100ms": (
        "How quickly force rises in the first 100 milliseconds of the pull — "
        "early explosive strength."
    ),
    "RFD - 150ms": (
        "How quickly force rises in the first 150 milliseconds of the pull."
    ),
    "Mean RSI (Jump Height/Contact Time)": (
        "Average hop reactivity across trials (jump height / contact time)."
    ),
}

# Context shown under each Looker-style VALD chart card.
VALD_CARD_CONTEXT: dict[str, str] = {
    "imtp_force_trend": (
        "Trend of peak isometric force / body mass over recent tests. "
        "Rising values usually indicate improving lower-body strength capacity."
    ),
    "imtp_rfd150_bilat": (
        "Left vs right rate of force development early in the pull. "
        "Large asymmetry can highlight side-to-side differences to monitor."
    ),
    "hj_rsi_trend": (
        "Hop reactive strength index over time. Higher RSI generally reflects "
        "faster, springier rebound off the ground."
    ),
    "hj_force_bilat": (
        "Peak force by limb during hops. Use the asymmetry line to spot "
        "persistent left/right differences."
    ),
    "cmj_jh_trend": (
        "Countermovement jump height history. A primary marker of lower-body "
        "power expression in a sport-relevant movement."
    ),
    "cmj_rsi_trend": (
        "CMJ RSI-modified over time — jump output relative to time on the "
        "ground in the countermovement."
    ),
    "sj_jh_trend": (
        "Squat jump height history (little/no countermovement). Useful for "
        "concentric-only power without the stretch-shortening assist."
    ),
    "sj_rfd_bilat": (
        "Concentric rate of force development by limb in the squat jump. "
        "Asymmetry flags uneven push-off contributions."
    ),
}

# Context shown under each HitTrax / batted-ball chart.
HITTRAX_CHART_CONTEXT: dict[str, str] = {
    "zone_ev": (
        "Average exit velocity by pitch location (13-zone). Hotter cells are "
        "where the hitter is driving the ball hardest; dots are individual contacts."
    ),
    "zone_la": (
        "Average launch angle by pitch location. Use with the EV zone chart to "
        "see whether hard contact is also leaving at a useful trajectory."
    ),
    "zone_poi": (
        "Average Point of Impact (depth of contact) by pitch location. Negative = "
        "deeper / toward the catcher; positive = out in front of the plate. Same "
        "13-zone layout as the EV/LA charts."
    ),
    "plate_vert": (
        "Catcher's view of contact height × lateral location (inch markers), "
        "colored by EV. Dashed box is the strike zone; clustering shows where "
        "balls are being attacked."
    ),
    "plate_horiz": (
        "EV by depth of contact (inch markers): lateral location × depth relative "
        "to the plate. Positive depth is out in front; band mph callouts summarize "
        "average exit velo at each depth."
    ),
    "ev_la": (
        "Each batted ball is plotted twice: filled circles are exit velocity (left axis), "
        "open diamonds are distance (right axis). X is launch angle. Color is flight type "
        "(GB / LD / FB / PU). No session-level fit — a larger-sample model comes later."
    ),
    "spray": (
        "Field spray of batted balls (direction × distance), colored by exit velocity. "
        "Arcs are distance from home (ft). Shows pull/oppo tendency and how hard contact "
        "is distributed across the field."
    ),
}


def fetch_vald_metric_definitions(
    conn, metric_names: list[str]
) -> dict[str, dict[str, str]]:
    """
    Look up descriptions from PlayerDev.vald_dictionary (loaded by
    ReplayPlayerDev/APIs/Vald_Metric_Definitions.py).

    Returns {metric_name: {description, unit}} for names that resolve.
    """
    names = [n for n in dict.fromkeys(metric_names) if n]
    if not names:
        return {}
    try:
        placeholders = ", ".join(["%s"] * len(names))
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                f"""
                SELECT metric_name, description, unit
                FROM vald_dictionary
                WHERE metric_name IN ({placeholders})
                """,
                tuple(names),
            )
            rows = cur.fetchall() or []
    except Exception as e:
        logger.warning("vald_dictionary lookup failed: %s", e)
        return {}

    out: dict[str, dict[str, str]] = {}
    for row in rows:
        name = (row.get("metric_name") or "").strip()
        if not name:
            continue
        desc = (row.get("description") or "").strip()
        unit = (row.get("unit") or "").strip()
        if desc:
            out[name] = {"description": desc, "unit": unit}
    return out


def vald_definitions_for_report(conn) -> list[dict[str, str]]:
    """
    Definition rows for each VALD table metric (one per report label).

    Includes report_label so the PDF can place the description next to
    the matching comparison-table row.
    """
    lookup_names = [vald_name for _, vald_name in VALD_METRIC_DEF_KEYS]
    db_defs = fetch_vald_metric_definitions(conn, lookup_names)
    rows: list[dict[str, str]] = []
    for report_label, vald_name in VALD_METRIC_DEF_KEYS:
        db = db_defs.get(vald_name) or {}
        desc = (db.get("description") or "").strip() or VALD_METRIC_DEF_FALLBACKS.get(
            vald_name, ""
        )
        if not desc:
            continue
        rows.append(
            {
                "report_label": report_label,
                "label": vald_name,
                "description": desc,
                "unit": (db.get("unit") or "").strip(),
            }
        )
    return rows


def metrics_for_assessment(conn, row: dict, *, include_chart_series: bool = False) -> dict[str, Any]:
    """Blast + HitTrax + VALD aggregates for a single hitting_assessments row."""
    start, end = _window_bounds(row)
    player = row["player_name"]
    used_blast = bool(row.get("used_blast", 1))
    used_hittrax = bool(row.get("used_hittrax", 1))
    used_vald = bool(row.get("used_vald", 1))
    blast_rows = _blast_rows(conn, player, start, end) if used_blast else []
    hittrax_rows = _hittrax_rows(conn, player, start, end) if used_hittrax else []
    hittrax_contacts = (
        hittrax_contacts_for_charts(hittrax_rows) if used_hittrax else []
    )
    out: dict[str, Any] = {
        "assessment_id": row["assessment_id"],
        "player_name": player,
        "assessment_type": row.get("assessment_type"),
        "assessment_date": row.get("assessment_date"),
        "retest_number": retest_number_for(conn, row),
        "notes": row.get("notes"),
        "video_analysis_url": row.get("video_analysis_url"),
        "start_ts": start,
        "end_ts": end,
        "used_blast": used_blast,
        "used_hittrax": used_hittrax,
        "used_vald": used_vald,
        "blast": aggregate_blast(blast_rows) if used_blast else {},
        "hittrax": aggregate_hittrax(hittrax_rows) if used_hittrax else {},
        "hittrax_breakdown": (
            aggregate_hittrax_location_breakdown(hittrax_contacts)
            if used_hittrax
            else {}
        ),
        "hittrax_contacts": hittrax_contacts if include_chart_series else [],
        "vald": aggregate_vald(conn, player, start, end) if used_vald else {},
    }
    if include_chart_series and used_vald:
        out["vald_series"] = vald_series_for_charts(conn, player, row.get("assessment_date"))
    elif include_chart_series:
        out["vald_series"] = {}
    return out


def build_report_bundle(conn, current: dict) -> dict[str, Any]:
    """
    current / previous / baseline metric blocks for PDF or API.

    previous comes from previous_assessment_id on the row.
    baseline is auto-resolved (earliest initial for the player).
    """
    peers = get_comparison_peers(conn, current)
    current_metrics = metrics_for_assessment(conn, current, include_chart_series=True)
    vald_defs = (
        vald_definitions_for_report(conn)
        if current_metrics.get("used_vald", True)
        else []
    )
    return {
        "current": current_metrics,
        "previous": metrics_for_assessment(conn, peers["previous"]) if peers["previous"] else None,
        "baseline": metrics_for_assessment(conn, peers["baseline"]) if peers["baseline"] else None,
        "notes": current.get("notes") or current_metrics.get("notes"),
        "video_analysis_url": (
            current.get("video_analysis_url")
            or current_metrics.get("video_analysis_url")
        ),
        "vald_definitions": vald_defs,
        "vald_card_context": VALD_CARD_CONTEXT,
        "hittrax_chart_context": HITTRAX_CHART_CONTEXT,
    }


def build_report_bundle_json(conn, current: dict) -> dict[str, Any]:
    """Same as build_report_bundle but with datetimes as strings for jsonify."""
    bundle = build_report_bundle(conn, current)
    return {
        "current": _serialize_side(bundle["current"]),
        "previous": _serialize_side(bundle["previous"]),
        "baseline": _serialize_side(bundle["baseline"]),
    }
