"""HitTrax + VALD chart images for HEAT assessment PDFs."""
from __future__ import annotations

import io
import logging
import math
import statistics
from typing import Any, Optional

logger = logging.getLogger(__name__)

NAVY = "#0b3d5c"
GOLD = "#fcba39"
MUTED = "#57606a"
DARK_BG = "#2c2e33"
LEFT_BLUE = "#3b82f6"
RIGHT_ORANGE = "#f97316"
M_TO_IN = 39.3701


def _png_bytes(fig, facecolor: str = "white") -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight", facecolor=facecolor)
    buf.seek(0)
    data = buf.read()
    buf.close()
    return data


def _close(fig) -> None:
    import matplotlib.pyplot as plt

    plt.close(fig)


def _ensure_mpl():
    import matplotlib

    matplotlib.use("Agg")


# ---------------------------------------------------------------------------
# HitTrax — existing light charts
# ---------------------------------------------------------------------------


def _ev_la_chart(contacts: list[dict[str, Any]]) -> Optional[bytes]:
    pts = [
        (c["launch_angle"], c["ev"])
        for c in contacts
        if c.get("launch_angle") is not None and c.get("ev") is not None
    ]
    if not pts:
        return None

    import matplotlib.pyplot as plt

    xs, ys = zip(*pts)
    fig, ax = plt.subplots(figsize=(4.2, 3.2))
    ax.scatter(xs, ys, c=GOLD, edgecolors=NAVY, s=36, alpha=0.85, linewidths=0.6)
    ax.axvspan(5, 15, color=NAVY, alpha=0.08, label="LA 5–15°")
    ax.set_xlabel("Launch angle (°)")
    ax.set_ylabel("Exit velocity (mph)")
    ax.set_title("Exit velocity × launch angle")
    ax.grid(True, linestyle=":", alpha=0.45)
    ax.legend(loc="best", fontsize=8, frameon=False)
    data = _png_bytes(fig)
    _close(fig)
    return data


def _spray_chart(contacts: list[dict[str, Any]]) -> Optional[bytes]:
    pts: list[tuple[float, float, float]] = []
    for c in contacts:
        ha = c.get("horz_angle")
        dist = c.get("distance")
        ev = c.get("ev")
        if ha is None or dist is None or dist <= 0:
            continue
        rad = math.radians(float(ha))
        x = float(dist) * math.sin(rad)
        y = float(dist) * math.cos(rad)
        pts.append((x, y, float(ev) if ev is not None else 0.0))
    if not pts:
        return None

    import matplotlib.pyplot as plt
    import numpy as np

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    evs = [p[2] for p in pts]

    fig, ax = plt.subplots(figsize=(4.2, 3.6))
    max_r = max(max(abs(x) for x in xs), max(ys), 50) * 1.1
    for ang in (-45, 45):
        r = math.radians(ang)
        ax.plot([0, max_r * math.sin(r)], [0, max_r * math.cos(r)], color=MUTED, lw=0.8, alpha=0.7)
    theta = np.linspace(math.radians(-45), math.radians(45), 60)
    for radius in (200, 300, 400):
        if radius > max_r:
            continue
        ax.plot(radius * np.sin(theta), radius * np.cos(theta), color=MUTED, lw=0.5, alpha=0.35)

    sc = ax.scatter(xs, ys, c=evs, cmap="YlOrRd", edgecolors=NAVY, s=40, linewidths=0.5, alpha=0.9)
    cbar = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("EV (mph)", fontsize=8)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-max_r, max_r)
    ax.set_ylim(-10, max_r)
    ax.set_xlabel("Pull ← → Oppo (ft)")
    ax.set_ylabel("Toward CF (ft)")
    ax.set_title("Spray chart")
    ax.grid(True, linestyle=":", alpha=0.35)
    data = _png_bytes(fig)
    _close(fig)
    return data


# ---------------------------------------------------------------------------
# HitTrax — zone heatmaps + depth (dark theme)
# ---------------------------------------------------------------------------


def _plate_xy(c: dict[str, Any]) -> Optional[tuple[float, float]]:
    """Normalized plate coords in ~[0,1]×[0,1] (catcher's view: x left→right, y low→high)."""
    x = c.get("intersect2")
    y = c.get("intersect3")
    if x is not None and y is not None:
        return float(x), float(y)
    # Fallback: scale PBH/PBV into 0–1 from observed HitTrax ranges
    pbh, pbv = c.get("pbh"), c.get("pbv")
    if pbh is None or pbv is None:
        return None
    # Peele sample ~ PBH 1.5–7.5, PBV 7.5–20
    nx = (float(pbh) - 1.0) / 7.0
    ny = (float(pbv) - 7.0) / 14.0
    return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))


def _assign_zone(x: float, y: float) -> str:
    """
    13-zone HitTrax-style layout on unit square.
    Core 3×3 for x,y in [0.25, 0.75]; four outer chase cells.
    """
    lo, hi = 0.25, 0.75
    if x < lo:
        return "outer_left"
    if x > hi:
        return "outer_right"
    if y > hi:
        return "outer_high"
    if y < lo:
        return "outer_low"
    # Inner 3×3
    col = 0 if x < lo + (hi - lo) / 3 else (1 if x < lo + 2 * (hi - lo) / 3 else 2)
    row = 0 if y > hi - (hi - lo) / 3 else (1 if y > lo + (hi - lo) / 3 else 2)
    # row 0 = high, 2 = low
    return f"z{row}{col}"


# Drawing order / geometry for dark zone chart (unit square coords)


def _core_rects() -> list[tuple[str, float, float, float, float]]:
    lo, hi = 0.25, 0.75
    step = (hi - lo) / 3
    out = []
    for row in range(3):  # 0 high → 2 low
        for col in range(3):
            x0 = lo + col * step
            # row 0 at top
            y0 = hi - (row + 1) * step
            out.append((f"z{row}{col}", x0, y0, step, step))
    return out


def _all_zone_rects() -> list[tuple[str, float, float, float, float]]:
    return [
        ("outer_left", 0.0, 0.25, 0.25, 0.50),
        ("outer_right", 0.75, 0.25, 0.25, 0.50),
        ("outer_high", 0.25, 0.75, 0.50, 0.25),
        ("outer_low", 0.25, 0.0, 0.50, 0.25),
        *_core_rects(),
    ]


def _zone_heatmap(contacts: list[dict[str, Any]], metric: str, title: str, unit: str) -> Optional[bytes]:
    buckets: dict[str, list[float]] = {z: [] for z, *_ in _all_zone_rects()}
    scatter: list[tuple[float, float, float]] = []
    for c in contacts:
        xy = _plate_xy(c)
        val = c.get(metric)
        if xy is None or val is None:
            continue
        x, y = xy
        zid = _assign_zone(x, y)
        buckets.setdefault(zid, []).append(float(val))
        scatter.append((x, y, float(val)))
    if not scatter:
        return None

    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    from matplotlib.patches import Rectangle

    avgs = {k: (sum(v) / len(v) if v else None) for k, v in buckets.items()}
    present = [a for a in avgs.values() if a is not None]
    if not present:
        return None
    vmin, vmax = min(present), max(present)
    if abs(vmax - vmin) < 1e-6:
        vmax = vmin + 1.0
    cmap = plt.get_cmap("coolwarm")
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

    fig, ax = plt.subplots(figsize=(4.0, 4.0), facecolor=DARK_BG)
    ax.set_facecolor(DARK_BG)
    total_n = len(scatter)

    for zid, x0, y0, w, h in _all_zone_rects():
        avg = avgs.get(zid)
        n = len(buckets.get(zid) or [])
        if avg is None:
            color = "#4a4a4a"
        else:
            color = cmap(norm(avg))
        ax.add_patch(
            Rectangle((x0, y0), w, h, facecolor=color, edgecolor="white", linewidth=0.8, alpha=0.9)
        )
        if avg is not None:
            label = f"{avg:.0f} {unit}\n{n}/{total_n}"
            ax.text(
                x0 + w / 2,
                y0 + h / 2,
                label,
                ha="center",
                va="center",
                color="white",
                fontsize=7,
                fontweight="bold",
            )

    # Contact overlay
    xs = [p[0] for p in scatter]
    ys = [p[1] for p in scatter]
    ax.scatter(xs, ys, c="#60a5fa", s=12, alpha=0.55, edgecolors="none", zorder=5)

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("white")
        spine.set_linewidth(1.2)
    ax.set_title(title, color="white", fontsize=11, pad=8)
    fig.patch.set_facecolor(DARK_BG)
    data = _png_bytes(fig, facecolor=DARK_BG)
    _close(fig)
    return data


def _ev_depth_chart(contacts: list[dict[str, Any]]) -> Optional[bytes]:
    """Depth of contact from Intersect1 (m → inches); home plate at 0."""
    pts: list[tuple[float, float, float]] = []  # depth_in, plate_x, ev
    for c in contacts:
        i1 = c.get("intersect1")
        ev = c.get("ev")
        if i1 is None or ev is None:
            continue
        depth_in = float(i1) * M_TO_IN
        xy = _plate_xy(c)
        px = (xy[0] - 0.5) if xy else 0.0  # center plate
        pts.append((depth_in, px * 17.0, float(ev)))  # ~plate width inches scale
    if not pts:
        return None

    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    depths = [p[0] for p in pts]
    xs = [p[1] for p in pts]
    evs = [p[2] for p in pts]

    fig, ax = plt.subplots(figsize=(4.0, 4.2), facecolor=DARK_BG)
    ax.set_facecolor(DARK_BG)

    # Home plate glyph near depth = 0
    plate = Polygon(
        [(-8.5, -2), (-8.5, 4), (0, 8), (8.5, 4), (8.5, -2)],
        closed=True,
        facecolor="#9ca3af",
        edgecolor="white",
        alpha=0.3,
        linewidth=1.0,
        zorder=1,
    )
    ax.add_patch(plate)

    band_edges = list(range(-24, 25, 6))
    for y in band_edges:
        ax.axhline(y, color="white", lw=0.4, alpha=0.35, zorder=2)
        ax.text(-16, y, f"{y} IN", color="white", fontsize=6, va="center", ha="right")

    # Avg EV per band
    for i in range(len(band_edges) - 1):
        lo_b, hi_b = band_edges[i], band_edges[i + 1]
        band_evs = [e for d, _, e in pts if lo_b <= d < hi_b]
        mid = (lo_b + hi_b) / 2
        if band_evs:
            ax.text(
                16,
                mid,
                f"{sum(band_evs) / len(band_evs):.1f} mph",
                color="white",
                fontsize=6,
                va="center",
                ha="left",
            )
        else:
            ax.text(16, mid, "0.0 mph", color="#9ca3af", fontsize=6, va="center", ha="left")

    sc = ax.scatter(
        xs,
        depths,
        c=evs,
        cmap="turbo",
        s=28,
        alpha=0.9,
        edgecolors="white",
        linewidths=0.3,
        zorder=4,
    )
    cbar = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.08)
    cbar.ax.yaxis.set_tick_params(color="white", labelcolor="white")
    cbar.outline.set_edgecolor("white")
    cbar.set_label("EV (mph)", color="white", fontsize=8)

    y_pad = max(abs(min(depths)), abs(max(depths)), 18) + 4
    ax.set_ylim(-y_pad, y_pad)
    ax.set_xlim(-18, 18)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("white")
    ax.set_title("EV by Depth of Contact", color="white", fontsize=11, pad=8)
    ax.set_ylabel("Depth (in)", color="white", fontsize=8)
    ax.yaxis.label.set_color("white")
    fig.patch.set_facecolor(DARK_BG)
    data = _png_bytes(fig, facecolor=DARK_BG)
    _close(fig)
    return data


def build_hittrax_chart_images(contacts: list[dict[str, Any]]) -> dict[str, bytes]:
    """
    Keys: ev_la, spray, zone_ev, zone_la, ev_depth
    (legacy PBH scatter omitted — zone heatmaps replace it)
    """
    _ensure_mpl()
    out: dict[str, bytes] = {}
    if not contacts:
        return out

    builders = [
        ("ev_la", lambda: _ev_la_chart(contacts)),
        ("spray", lambda: _spray_chart(contacts)),
        ("zone_ev", lambda: _zone_heatmap(contacts, "ev", "Average Exit Velocity by Zone", "mph")),
        (
            "zone_la",
            lambda: _zone_heatmap(contacts, "launch_angle", "Average Launch Angle by Zone", "°"),
        ),
        ("ev_depth", lambda: _ev_depth_chart(contacts)),
    ]
    for key, fn in builders:
        try:
            img = fn()
            if img:
                out[key] = img
        except Exception:
            logger.exception("%s chart failed", key)
    return out


# ---------------------------------------------------------------------------
# VALD Looker-style cards
# ---------------------------------------------------------------------------


def _series_values(points: list[dict[str, Any]], metric: str, scale: float = 1.0) -> list[tuple[str, float]]:
    out = []
    for p in points:
        v = p.get(metric)
        if v is not None and p.get("date"):
            out.append((p["date"], float(v) * scale))
    return out


def _pct_change(curr: float, prev: Optional[float]) -> Optional[float]:
    if prev is None or prev == 0:
        return None
    return 100.0 * (curr - prev) / abs(prev)


def _rsi_scale(points: list[dict[str, Any]], metric: str) -> float:
    """ForceDecks sometimes stores RSI ×100 (e.g. 70.0 for 0.70)."""
    vals = [float(p[metric]) for p in points if p.get(metric) is not None]
    if vals and statistics.mean(vals) > 5:
        return 0.01
    return 1.0


def _trend_card(
    title: str,
    metric_label: str,
    points: list[dict[str, Any]],
    metric: str,
    unit: str = "",
    scale: Optional[float] = None,
) -> Optional[bytes]:
    if scale is None:
        scale = _rsi_scale(points, metric) if "RSI" in metric or "RSI" in metric_label else 1.0
    series = _series_values(points, metric, scale=scale)
    if not series:
        return None

    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    dates = [s[0] for s in series]
    vals = [s[1] for s in series]
    curr = vals[-1]
    prev = vals[-2] if len(vals) >= 2 else None
    pct = _pct_change(curr, prev)

    # Taller card + reserved header band so title never collides with the sparkline
    fig = plt.figure(figsize=(5.0, 2.6), facecolor="white")
    gs = GridSpec(
        2,
        2,
        figure=fig,
        height_ratios=[0.55, 2.0],
        width_ratios=[1.0, 1.35],
        hspace=0.35,
        wspace=0.28,
        left=0.06,
        right=0.96,
        top=0.92,
        bottom=0.14,
    )
    ax_header = fig.add_subplot(gs[0, :])
    ax_l = fig.add_subplot(gs[1, 0])
    ax_r = fig.add_subplot(gs[1, 1])

    ax_header.set_xlim(0, 1)
    ax_header.set_ylim(0, 1)
    ax_header.axis("off")
    ax_header.text(0.0, 0.95, title, fontsize=8.5, fontweight="bold", color="#374151", va="top")
    ax_header.text(0.0, 0.25, metric_label, fontsize=6.5, color=MUTED, va="top", wrap=True)

    ax_l.set_xlim(0, 1)
    ax_l.set_ylim(0, 1)
    ax_l.axis("off")
    unit_s = f" {unit}" if unit else ""
    # Value + unit on one baseline; leave room below for delta badge
    ax_l.text(
        0.0,
        0.72,
        f"{curr:.2f}",
        fontsize=16,
        fontweight="bold",
        color="#111827",
        va="center",
        ha="left",
    )
    if unit_s.strip():
        ax_l.text(0.0, 0.48, unit_s.strip(), fontsize=7.5, color=MUTED, va="center", ha="left")
    if pct is not None:
        color = "#16a34a" if pct >= 0 else "#dc2626"
        arrow = "↑" if pct >= 0 else "↓"
        ax_l.text(
            0.0,
            0.18,
            f"{arrow} {abs(pct):.0f}%",
            fontsize=8.5,
            fontweight="bold",
            color=color,
            va="center",
            bbox=dict(
                boxstyle="round,pad=0.25",
                facecolor="#ecfdf5" if pct >= 0 else "#fef2f2",
                edgecolor="none",
            ),
        )

    ax_r.plot(range(len(vals)), vals, color="#4b5563", lw=1.4, marker="o", markersize=3.5)
    ax_r.plot(
        len(vals) - 1,
        curr,
        "o",
        color="#dc2626",
        markersize=6,
        fillstyle="none",
        markeredgewidth=1.4,
    )
    ax_r.fill_between(range(len(vals)), vals, alpha=0.08, color="#dc2626")
    ax_r.set_xticks([])
    ax_r.tick_params(axis="y", labelsize=6, colors=MUTED, pad=1)
    ax_r.spines["top"].set_visible(False)
    ax_r.spines["right"].set_visible(False)
    ax_r.spines["bottom"].set_visible(False)
    ax_r.spines["left"].set_color("#e5e7eb")
    ax_r.grid(True, axis="y", linestyle=":", alpha=0.35)
    ax_r.margins(x=0.08, y=0.18)
    if len(dates) >= 2:
        ax_r.set_xlabel(f"{dates[0][5:]} → {dates[-1][5:]}", fontsize=5.5, color=MUTED, labelpad=2)

    fig.patches.extend(
        [
            plt.Rectangle(
                (0.015, 0.03),
                0.97,
                0.94,
                transform=fig.transFigure,
                fill=False,
                edgecolor="#d1d5db",
                linewidth=1.0,
                zorder=-1,
            )
        ]
    )
    data = _png_bytes(fig)
    _close(fig)
    return data


def _bilateral_card(
    title: str,
    metric_label: str,
    points: list[dict[str, Any]],
    left_key: str,
    right_key: str,
    asym_key: Optional[str] = None,
    unit: str = "",
) -> Optional[bytes]:
    if not points:
        return None
    last = points[-1]
    left = last.get(left_key)
    right = last.get(right_key)
    if left is None and right is None:
        return None
    left_f = float(left) if left is not None else 0.0
    right_f = float(right) if right is not None else 0.0
    asym = last.get(asym_key) if asym_key else None
    if asym is None and left is not None and right is not None and (left_f + right_f) > 0:
        asym = 100.0 * abs(left_f - right_f) / (left_f + right_f)

    prev = points[-2] if len(points) >= 2 else None
    left_pct = _pct_change(
        left_f, float(prev[left_key]) if prev and prev.get(left_key) is not None else None
    )
    right_pct = _pct_change(
        right_f, float(prev[right_key]) if prev and prev.get(right_key) is not None else None
    )

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.0, 2.6), facecolor="white")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # Header band
    ax.text(0.04, 0.92, title, fontsize=8.5, fontweight="bold", color="#374151", va="top")
    ax.text(0.04, 0.78, metric_label, fontsize=6.5, color=MUTED, va="top")

    # Left column
    ax.text(0.22, 0.62, "Left", fontsize=7.5, color=LEFT_BLUE, fontweight="bold", ha="center")
    ax.text(0.22, 0.48, f"{left_f:.0f}", fontsize=13, fontweight="bold", color="#111827", ha="center")
    if unit:
        ax.text(0.22, 0.36, unit, fontsize=6.5, color=MUTED, ha="center")
    if left_pct is not None:
        c = "#16a34a" if left_pct >= 0 else "#dc2626"
        ax.text(
            0.22,
            0.24,
            f"{'↑' if left_pct >= 0 else '↓'} {abs(left_pct):.0f}%",
            fontsize=7.5,
            color=c,
            ha="center",
            fontweight="bold",
        )

    ax.plot([0.42, 0.42], [0.20, 0.64], color="#e5e7eb", lw=1, solid_capstyle="round")

    # Right column
    ax.text(0.62, 0.62, "Right", fontsize=7.5, color=RIGHT_ORANGE, fontweight="bold", ha="center")
    ax.text(0.62, 0.48, f"{right_f:.0f}", fontsize=13, fontweight="bold", color="#111827", ha="center")
    if unit:
        ax.text(0.62, 0.36, unit, fontsize=6.5, color=MUTED, ha="center")
    if right_pct is not None:
        c = "#16a34a" if right_pct >= 0 else "#dc2626"
        ax.text(
            0.62,
            0.24,
            f"{'↑' if right_pct >= 0 else '↓'} {abs(right_pct):.0f}%",
            fontsize=7.5,
            color=c,
            ha="center",
            fontweight="bold",
        )

    # Asymmetry bar — dedicated bottom strip, clear of value text
    if asym is not None:
        asym_f = abs(float(asym))
        ax.text(0.04, 0.09, "Asymmetry", fontsize=6, color=MUTED, va="center")
        bar_x, bar_w, bar_y, bar_h = 0.22, 0.58, 0.055, 0.045
        ax.add_patch(plt.Rectangle((bar_x, bar_y), bar_w, bar_h, facecolor="#e5e7eb", edgecolor="none"))
        fill = min(asym_f / 100.0, 1.0) * bar_w
        ax.add_patch(plt.Rectangle((bar_x, bar_y), fill, bar_h, facecolor=LEFT_BLUE, edgecolor="none"))
        # Place % label to the right of the bar track to avoid sitting on the fill edge
        ax.text(bar_x + bar_w + 0.02, 0.078, f"{asym_f:.0f}%", fontsize=6.5, color="#111827", va="center")

    fig.patches.extend(
        [
            plt.Rectangle(
                (0.015, 0.03),
                0.97,
                0.94,
                transform=fig.transFigure,
                fill=False,
                edgecolor="#d1d5db",
                linewidth=1.0,
                zorder=-1,
            )
        ]
    )
    data = _png_bytes(fig)
    _close(fig)
    return data


def build_vald_chart_images(series: dict[str, Any]) -> dict[str, bytes]:
    """
    Looker-style VALD cards. Keys are stable ids for PDF layout:
      imtp_force_trend, imtp_rfd150_bilat, hj_rsi_trend, hj_force_bilat,
      cmj_jh_trend, cmj_rsi_trend, sj_jh_trend, sj_rfd_bilat
    """
    _ensure_mpl()
    out: dict[str, bytes] = {}
    if not series:
        return out

    specs: list[tuple[str, Any]] = [
        (
            "imtp_force_trend",
            lambda: _trend_card(
                "Isometric Mid-Thigh Pull",
                "Peak Vertical Force / BM",
                series.get("imtp") or [],
                "Peak Vertical Force / BM",
                "N/kg",
            ),
        ),
        (
            "imtp_rfd150_bilat",
            lambda: _bilateral_card(
                "Isometric Mid-Thigh Pull",
                "Max RFD - 150ms - Left & Right Side",
                series.get("imtp") or [],
                "RFD - 150ms (Left)",
                "RFD - 150ms (Right)",
                "RFD - 150ms Asym (%)",
                "N/s",
            ),
        ),
        (
            "hj_rsi_trend",
            lambda: _trend_card(
                "Hop Test",
                "Mean RSI (Jump Height/Contact Time)",
                series.get("hj") or [],
                "Mean RSI (Jump Height/Contact Time)",
                "m/s",
            ),
        ),
        (
            "hj_force_bilat",
            lambda: _bilateral_card(
                "Hop Test",
                "Best Peak Force - Left & Right Side",
                series.get("hj") or [],
                "Best Peak Force (Left)",
                "Best Peak Force (Right)",
                "Best Peak Force (Asym)",
                "N",
            ),
        ),
        (
            "cmj_jh_trend",
            lambda: _trend_card(
                "Countermovement Jump",
                "Jump Height (Flight Time)",
                series.get("cmj") or [],
                "Jump Height (Flight Time)",
                "cm",
            ),
        ),
        (
            "cmj_rsi_trend",
            lambda: _trend_card(
                "Countermovement Jump",
                "RSI-modified",
                series.get("cmj") or [],
                "RSI-modified",
                "",
            ),
        ),
        (
            "sj_jh_trend",
            lambda: _trend_card(
                "Squat Jump",
                "Jump Height (Flight Time)",
                series.get("sj") or [],
                "Jump Height (Flight Time)",
                "cm",
            ),
        ),
        (
            "sj_rfd_bilat",
            lambda: _bilateral_card(
                "Squat Jump",
                "Concentric RFD - Left & Right Side",
                series.get("sj") or [],
                "Concentric RFD (Left)",
                "Concentric RFD (Right)",
                "Concentric RFD Asym (%)",
                "N/s",
            ),
        ),
    ]

    # Fallbacks when preferred metric empty
    fallbacks = {
        "hj_rsi_trend": lambda: _trend_card(
            "Hop Test",
            "Best RSI (Jump Height/Contact Time)",
            series.get("hj") or [],
            "Best RSI (Jump Height/Contact Time)",
            "m/s",
        ),
        "imtp_rfd150_bilat": lambda: _bilateral_card(
            "Isometric Mid-Thigh Pull",
            "Max RFD - 100ms - Left & Right Side",
            series.get("imtp") or [],
            "RFD - 100ms (Left)",
            "RFD - 100ms (Right)",
            "RFD - 100ms Asym (%)",
            "N/s",
        ),
    }

    for key, fn in specs:
        try:
            img = fn()
            if not img and key in fallbacks:
                img = fallbacks[key]()
            if img:
                out[key] = img
        except Exception:
            logger.exception("VALD card %s failed", key)
    return out
