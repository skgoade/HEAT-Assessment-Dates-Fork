"""Wellness questionnaire block for the HEAT PDF (PlayerDev.wellness_responses)."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from report_charts import _close, _ensure_mpl, _png_bytes

logger = logging.getLogger(__name__)

EXPECTED_CHECKINS_PER_WEEK = 2

METRIC_COLS = [
    "fatigue",
    "sleep_duration",
    "general_muscle_soreness",
    "stress",
    "diet",
    "arm_readiness",
]
METRIC_LABELS = {
    "fatigue": "Fatigue / Freshness",
    "sleep_duration": "Sleep Duration",
    "general_muscle_soreness": "Muscle Readiness",
    "stress": "Stress Level",
    "diet": "Nutrition",
    "arm_readiness": "Arm Readiness",
}
METRIC_COLORS = {
    "fatigue": "#eab308",
    "sleep_duration": "#2563eb",
    "general_muscle_soreness": "#f59e0b",
    "stress": "#e11d48",
    "diet": "#7c3aed",
    "arm_readiness": "#0d9488",
}
WELLNESS_COLORS = {
    "navy": "#14213d",
    "good": "#22a55e",
    "moderate": "#f6b93b",
    "low": "#ef4444",
    "good_bg": "#d1f2df",
    "moderate_bg": "#fdecc8",
    "low_bg": "#fbd5d5",
}


def _as_date(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    text = str(val).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _leading_num(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(str(raw).split(" - ", 1)[0].strip())
    except (TypeError, ValueError):
        return None


SCORE_SCALE_LABELS = (
    (1.5, "Very Poor"),
    (2.5, "Poor"),
    (3.5, "Average"),
    (4.5, "Good"),
    (5.1, "Excellent"),
)
SCORE_SCALE_HEX = {
    "Very Poor": WELLNESS_COLORS["low"],
    "Poor": "#f97316",
    "Average": WELLNESS_COLORS["moderate"],
    "Good": "#65a30d",
    "Excellent": WELLNESS_COLORS["good"],
}
SCORE_SCALE_BG = {
    "Very Poor": WELLNESS_COLORS["low_bg"],
    "Poor": "#fed7aa",
    "Average": WELLNESS_COLORS["moderate_bg"],
    "Good": "#d9f99d",
    "Excellent": WELLNESS_COLORS["good_bg"],
}
SCORE_BAND_ORDER = ("Excellent", "Good", "Average", "Poor", "Very Poor")


def _score_bucket(v: float) -> str:
    return score_band_label(v)


def score_band_label(v: Optional[float]) -> str:
    """Card-foot label on the 1–5 scale (Very Poor … Excellent)."""
    if v is None:
        return "—"
    for cut, label in SCORE_SCALE_LABELS:
        if v < cut:
            return label
    return "Excellent"


def score_band_hex(v: Optional[float]) -> str:
    if v is None:
        return "#6b7280"
    return SCORE_SCALE_HEX.get(score_band_label(v), "#6b7280")


def score_fill_hex(v: Optional[float]) -> str:
    if v is None:
        return WELLNESS_COLORS["navy"]
    if v >= 3.0:
        return WELLNESS_COLORS["good"]
    return WELLNESS_COLORS["low"]


def bucket_bg_hex(v: Optional[float]) -> str:
    if v is None:
        return "#f3f4f6"
    return SCORE_SCALE_BG.get(score_band_label(v), "#f3f4f6")


def fetch_wellness_rows(
    conn,
    player_name: str,
    *,
    as_of: Any,
    since: Any = None,
) -> list[dict[str, Any]]:
    """Check-ins for this athlete through the assessment date (optional since)."""
    end_d = _as_date(as_of)
    if not end_d or not (player_name or "").strip():
        return []
    end_ts = datetime.combine(end_d, time(23, 59, 59))
    start_d = _as_date(since)
    sql = """
        SELECT id, timestamp, player_name, fatigue, sleep_duration,
               general_muscle_soreness, stress, diet, arm_readiness, bodyweight
        FROM wellness_responses
        WHERE LOWER(TRIM(player_name)) = LOWER(TRIM(%s))
          AND timestamp <= %s
    """
    params: list[Any] = [player_name, end_ts]
    if start_d:
        sql += " AND timestamp >= %s"
        params.append(datetime.combine(start_d, time.min))
    sql += " ORDER BY timestamp ASC"
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(sql, tuple(params))
            return list(cur.fetchall() or [])
    except Exception as e:
        logger.warning("wellness_responses query failed: %s", e)
        return []


def aggregate_wellness(
    rows: list[dict[str, Any]],
    *,
    block_start: Any = None,
    block_end: Any = None,
) -> dict[str, Any]:
    """Notebook-equivalent summary for the PDF wellness page."""
    parsed: list[dict[str, Any]] = []
    for row in rows:
        scores = {c: _leading_num(row.get(c)) for c in METRIC_COLS}
        if any(v is None for v in scores.values()):
            continue
        daily = sum(scores.values()) / len(METRIC_COLS)
        bw = row.get("bodyweight")
        try:
            bw_f = float(bw) if bw is not None else None
        except (TypeError, ValueError):
            bw_f = None
        ts = row.get("timestamp")
        parsed.append(
            {
                "timestamp": ts,
                "scores": scores,
                "daily_avg": daily,
                "bodyweight": bw_f,
                "bucket": _score_bucket(daily),
            }
        )
    empty = {
        "n_checkins": 0,
        "expected_checkins": 0,
        "compliance_pct": 0.0,
        "overall_avg": None,
        "metric_avgs": {c: None for c in METRIC_COLS},
        "bw_first": None,
        "bw_last": None,
        "bw_change": None,
        "date_start": None,
        "date_end": None,
        "bucket_counts": {k: 0 for k in SCORE_SCALE_HEX},
        "bucket_pcts": {k: 0 for k in SCORE_SCALE_HEX},
        "rows": [],
        "series": [],
    }
    if not parsed:
        return empty

    n = len(parsed)
    metric_avgs = {
        c: sum(p["scores"][c] for p in parsed) / n for c in METRIC_COLS
    }
    overall = sum(p["daily_avg"] for p in parsed) / n
    bws = [p["bodyweight"] for p in parsed if p["bodyweight"] is not None]
    bw_first = bws[0] if bws else None
    bw_last = bws[-1] if bws else None
    bw_change = (
        (bw_last - bw_first) if bw_first is not None and bw_last is not None else None
    )

    ts0 = parsed[0]["timestamp"]
    ts1 = parsed[-1]["timestamp"]
    start_d = _as_date(block_start) or _as_date(ts0)
    end_d = _as_date(block_end) or _as_date(ts1)
    if start_d and end_d and end_d >= start_d:
        weeks = max((end_d - start_d).days / 7.0, 1 / 7.0)
    else:
        weeks = n / EXPECTED_CHECKINS_PER_WEEK
    expected = max(1, round(weeks * EXPECTED_CHECKINS_PER_WEEK))
    counts = {k: 0 for k in SCORE_SCALE_HEX}
    for p in parsed:
        counts[p["bucket"]] += 1
    pcts = {k: int(round(100.0 * v / n)) for k, v in counts.items()}

    table_rows = []
    series = []
    for p in parsed:
        ts = p["timestamp"]
        day = _as_date(ts)
        table_rows.append(
            {
                "date": day,
                "fatigue": p["scores"]["fatigue"],
                "sleep": p["scores"]["sleep_duration"],
                "soreness": p["scores"]["general_muscle_soreness"],
                "stress": p["scores"]["stress"],
                "diet": p["scores"]["diet"],
                "arm": p["scores"]["arm_readiness"],
                "weight": p["bodyweight"],
                "wellness": round(p["daily_avg"], 1),
            }
        )
        series.append({"ts": ts, "daily_avg": p["daily_avg"]})

    return {
        "n_checkins": n,
        "expected_checkins": expected,
        "compliance_pct": 100.0 * n / expected,
        "overall_avg": overall,
        "metric_avgs": metric_avgs,
        "bw_first": bw_first,
        "bw_last": bw_last,
        "bw_change": bw_change,
        "date_start": start_d,
        "date_end": end_d,
        "bucket_counts": counts,
        "bucket_pcts": pcts,
        "rows": table_rows,
        "series": series,
    }


def wellness_for_assessment(
    conn,
    player_name: str,
    assessment_date: Any,
    previous_date: Any = None,
) -> dict[str, Any]:
    rows = fetch_wellness_rows(
        conn, player_name, as_of=assessment_date, since=previous_date
    )
    return aggregate_wellness(
        rows, block_start=previous_date or None, block_end=assessment_date
    )


def build_wellness_chart_images(block: dict[str, Any]) -> dict[str, bytes]:
    """Keys: trend, donut, recovery."""
    _ensure_mpl()
    out: dict[str, bytes] = {}
    if not block or not block.get("n_checkins"):
        return out
    trend = _trend_chart(block)
    if trend:
        out["trend"] = trend
    donut = _donut_chart(block)
    if donut:
        out["donut"] = donut
    bars = _recovery_bars(block)
    if bars:
        out["recovery"] = bars
    return out


def _trend_chart(block: dict[str, Any]) -> Optional[bytes]:
    series = block.get("series") or []
    if len(series) < 1:
        return None
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    xs = [p["ts"] for p in series]
    ys = [p["daily_avg"] for p in series]
    overall = block.get("overall_avg") or 0
    fig, ax = plt.subplots(figsize=(4.8, 1.7), dpi=140)
    bands = (
        (1.0, 1.5, "Very Poor"),
        (1.5, 2.5, "Poor"),
        (2.5, 3.5, "Average"),
        (3.5, 4.5, "Good"),
        (4.5, 5.25, "Excellent"),
    )
    for lo, hi, label in bands:
        ax.axhspan(lo, hi, color=SCORE_SCALE_HEX[label], alpha=0.12, zorder=0)
    ax.plot(
        xs,
        ys,
        color=WELLNESS_COLORS["navy"],
        linewidth=1.0,
        zorder=3,
    )
    ax.scatter(
        xs,
        ys,
        c=[score_band_hex(y) for y in ys],
        s=18,
        zorder=4,
        edgecolors="white",
        linewidths=0.4,
    )
    ax.axhline(overall, color="#9ca3af", linewidth=1.0, linestyle="--", zorder=2)
    ax.set_ylim(1, 5.25)
    pad = timedelta(days=2)
    try:
        ax.set_xlim(xs[0] - pad, xs[-1] + pad)
    except TypeError:
        pass
    ax.set_ylabel("SCORE (1–5)", fontsize=7, color="#6b7280")
    ax.tick_params(axis="both", labelsize=7, colors="#6b7280")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")

    def _fmt_date(x, _pos):
        dt = mdates.num2date(x)
        return f"{dt.strftime('%b')} {dt.day}"

    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_fmt_date))
    fig.tight_layout()
    data = _png_bytes(fig, facecolor="white")
    _close(fig)
    return data


def _donut_chart(block: dict[str, Any]) -> Optional[bytes]:
    import matplotlib.pyplot as plt

    counts = block.get("bucket_counts") or {}
    vals = [counts.get(k) or 0 for k in SCORE_BAND_ORDER]
    if sum(vals) <= 0:
        return None
    n = int(block.get("n_checkins") or 0)
    fig, ax = plt.subplots(figsize=(1.85, 1.85), dpi=140)
    pie_vals = [v for v in vals if v > 0]
    pie_colors = [SCORE_SCALE_HEX[k] for k, v in zip(SCORE_BAND_ORDER, vals) if v > 0]
    ax.pie(
        pie_vals,
        colors=pie_colors,
        startangle=90,
        counterclock=False,
        wedgeprops=dict(width=0.34, edgecolor="white", linewidth=2),
    )
    ax.text(
        0,
        0.12,
        str(n),
        ha="center",
        va="center",
        fontsize=16,
        fontweight="bold",
        color=WELLNESS_COLORS["navy"],
    )
    ax.text(
        0,
        -0.16,
        "CHECK-INS",
        ha="center",
        va="center",
        fontsize=6.5,
        color="#6b7280",
    )
    ax.set_aspect("equal")
    fig.tight_layout()
    data = _png_bytes(fig, facecolor="white")
    _close(fig)
    return data


def _recovery_bars(block: dict[str, Any]) -> Optional[bytes]:
    import matplotlib.pyplot as plt
    import numpy as np

    avgs = block.get("metric_avgs") or {}
    bar_order = [
        "arm_readiness",
        "sleep_duration",
        "general_muscle_soreness",
        "fatigue",
        "diet",
        "stress",
    ]
    bar_labels = [METRIC_LABELS[c].upper() for c in bar_order]
    bar_vals = [float(avgs[c]) if avgs.get(c) is not None else 0.0 for c in bar_order]
    bar_colors = [
        score_band_hex(v) if avgs.get(c) is not None else "#9ca3af"
        for c, v in zip(bar_order, bar_vals)
    ]
    fig, ax = plt.subplots(figsize=(7.0, 1.85), dpi=140)
    y_pos = np.arange(len(bar_order))[::-1]
    ax.barh(y_pos, bar_vals, color=bar_colors, height=0.58, zorder=3)
    for yi, v in zip(y_pos, bar_vals):
        ax.text(
            v + 0.08,
            yi,
            f"{v:.1f}",
            va="center",
            fontsize=8,
            fontweight="bold",
            color=WELLNESS_COLORS["navy"],
        )
    ax.set_yticks(y_pos)
    ax.set_yticklabels(bar_labels, fontsize=7.5, color=WELLNESS_COLORS["navy"])
    ax.set_xlim(1, 5)
    ax.set_xlabel(
        "SCORE (1 = VERY POOR, 5 = EXCELLENT)", fontsize=7, color="#6b7280"
    )
    ax.tick_params(axis="x", labelsize=7, colors="#6b7280")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color("#d1d5db")
    ax.grid(axis="x", color="#e5e7eb", linewidth=0.6, zorder=0)
    fig.tight_layout()
    data = _png_bytes(fig, facecolor="white")
    _close(fig)
    return data
