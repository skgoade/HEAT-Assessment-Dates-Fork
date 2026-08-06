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


# Ball-flight bins (launch angle °) — simple point model for insight
FLIGHT_BINS: list[tuple[str, float, float, str]] = [
    ("GB", float("-inf"), 10.0, "#6b7280"),
    ("LD", 10.0, 25.0, "#2563eb"),
    ("FB", 25.0, 50.0, "#16a34a"),
    ("PU", 50.0, float("inf"), "#f59e0b"),
]
MIN_FIT_N = 8
PEAK_LA_HALF_WIDTH = 5.0  # shade ±5° around peak predicted distance LA


def _ols_linear(xs: list[float], ys: list[float]) -> Optional[tuple[float, float]]:
    """Return (intercept, slope) for y = a + b x, or None."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x < 1e-12:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    b = cov / var_x
    a = mean_y - b * mean_x
    return a, b


def _ols_quadratic(xs: list[float], ys: list[float]) -> Optional[tuple[float, float, float]]:
    """
    Dist ~ a + b·LA + c·LA² via normal equations (3×3).
    Vertex LA = -b/(2c) when c < 0 → useful peak-distance launch angle.
    """
    n = len(xs)
    if n < 4:
        return None
    # Design matrix columns: 1, x, x²
    s0 = float(n)
    s1 = sum(xs)
    s2 = sum(x * x for x in xs)
    s3 = sum(x ** 3 for x in xs)
    s4 = sum(x ** 4 for x in xs)
    t0 = sum(ys)
    t1 = sum(x * y for x, y in zip(xs, ys))
    t2 = sum(x * x * y for x, y in zip(xs, ys))
    # Solve [[s0,s1,s2],[s1,s2,s3],[s2,s3,s4]] [a,b,c]^T = [t0,t1,t2]
    A = [[s0, s1, s2], [s1, s2, s3], [s2, s3, s4]]
    bvec = [t0, t1, t2]
    try:
        # Gaussian elimination
        M = [A[i][:] + [bvec[i]] for i in range(3)]
        for col in range(3):
            piv = max(range(col, 3), key=lambda r: abs(M[r][col]))
            if abs(M[piv][col]) < 1e-12:
                return None
            M[col], M[piv] = M[piv], M[col]
            div = M[col][col]
            for j in range(col, 4):
                M[col][j] /= div
            for r in range(3):
                if r == col:
                    continue
                factor = M[r][col]
                for j in range(col, 4):
                    M[r][j] -= factor * M[col][j]
        a, b, c = M[0][3], M[1][3], M[2][3]
        return a, b, c
    except Exception:
        return None


def _ev_la_chart(contacts: list[dict[str, Any]]) -> Optional[bytes]:
    """
    EV × LA with:
      (A) Optimal LA from hang-time proxy EV̂(θ)·sin(θ) using the EV~LA fit
          (trainer-aligned; peaks near ~80° when EV declines gently with LA)
      (B) Points colored by ball-flight bin (GB / LD / FB / PU)
      (C) EV~LA linear trend on the primary axis

    Legend + fit notes sit outside the axes so they don't cover points.
    """
    rows: list[tuple[float, float, Optional[float]]] = []
    for c in contacts:
        la = c.get("launch_angle")
        ev = c.get("ev")
        if la is None or ev is None:
            continue
        dist = c.get("distance")
        try:
            rows.append(
                (
                    float(la),
                    float(ev),
                    float(dist) if dist is not None else None,
                )
            )
        except (TypeError, ValueError):
            continue
    if not rows:
        return None

    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    las = [r[0] for r in rows]
    evs = [r[1] for r in rows]
    fig, ax = plt.subplots(figsize=(4.6, 4.2))

    # --- (B) flight-type scatter ---
    for name, lo, hi, color in FLIGHT_BINS:
        xs = [la for la, ev, _ in rows if lo <= la < hi]
        ys = [ev for la, ev, _ in rows if lo <= la < hi]
        if xs:
            ax.scatter(
                xs,
                ys,
                c=color,
                edgecolors=NAVY,
                s=34,
                alpha=0.88,
                linewidths=0.5,
                zorder=3,
                label=name,
            )

    note_bits: list[str] = []
    peak_la: Optional[float] = None

    if len(rows) >= MIN_FIT_N:
        # Linear EV ~ LA trend on primary axis
        lin = _ols_linear(las, evs)
        if lin is not None:
            a_e, b_e = lin
            la_min, la_max = min(las), max(las)
            # Trend line across observed LA span
            ax.plot(
                [la_min, la_max],
                [a_e + b_e * la_min, a_e + b_e * la_max],
                color=NAVY,
                lw=1.4,
                alpha=0.75,
                zorder=2,
                label="EV fit",
            )
            note_bits.append(f"EV≈{a_e:.0f}{b_e:+.2f}·LA")

            # Hang-time / lift proxy: EV̂(θ)·sin(θ). With a gently declining EV
            # fit this peaks near ~80°, matching trainer intuition better than
            # peak predicted carry (~30–40°).
            grid = [i * 0.5 for i in range(0, 181)]  # 0° … 90°
            hang = []
            for x in grid:
                ev_hat = a_e + b_e * x
                hang.append(max(0.0, ev_hat) * math.sin(math.radians(x)) if ev_hat > 0 else 0.0)
            ax2 = ax.twinx()
            ax2.plot(grid, hang, color="#0ea5e9", lw=1.6, alpha=0.85, zorder=2)
            ax2.set_ylabel("Hang-time proxy (EV·sin LA)", fontsize=8, color="#0284c7")
            ax2.tick_params(axis="y", labelsize=7, colors="#0284c7")
            ax2.spines["right"].set_color("#7dd3fc")
            ax2.set_ylim(bottom=0)

            if any(v > 0 for v in hang):
                peak_la = grid[max(range(len(hang)), key=lambda i: hang[i])]
                lo_b = peak_la - PEAK_LA_HALF_WIDTH
                hi_b = peak_la + PEAK_LA_HALF_WIDTH
                ax.axvspan(lo_b, hi_b, color=NAVY, alpha=0.16, zorder=0, linewidth=0)
                ax.axvline(peak_la, color=NAVY, lw=1.0, ls="--", alpha=0.7, zorder=1)
                ax.text(
                    peak_la,
                    max(evs),
                    f" optimal LA\n {peak_la:.0f}°",
                    ha="center",
                    va="top",
                    fontsize=7,
                    color=NAVY,
                    alpha=0.9,
                    zorder=4,
                )
                note_bits.append(f"Optimal LA ≈ {peak_la:.0f}°")
    else:
        # Sparse day: soft reference band only
        ax.axvspan(5, 15, color=NAVY, alpha=0.12, zorder=0, linewidth=0)
        note_bits.append("n low — no fit")

    ax.set_xlabel("Launch angle (°)")
    ax.set_ylabel("Exit velocity (mph)")
    ax.set_title("Exit velocity × launch angle", pad=10)
    ax.grid(True, linestyle=":", alpha=0.4)
    # Allow the hang-time curve / optimal marker to extend past observed LAs
    x_right = max(las)
    if peak_la is not None:
        x_right = max(x_right, peak_la + 8, 90)
    ax.set_xlim(min(min(las), -5), x_right)

    # Legend + equation outside the axes (above / below) so points stay readable
    flight_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            color="w",
            markerfacecolor=col,
            markeredgecolor=NAVY,
            markersize=7,
            label=name,
        )
        for name, _, _, col in FLIGHT_BINS
    ]
    fig.legend(
        handles=flight_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.98),
        ncol=4,
        fontsize=7,
        frameon=False,
        title="Flight",
        title_fontsize=8,
        borderaxespad=0,
    )
    if note_bits:
        fig.text(
            0.5,
            0.02,
            " · ".join(note_bits),
            ha="center",
            va="bottom",
            fontsize=8,
            color=NAVY,
            alpha=0.95,
        )
    fig.subplots_adjust(top=0.82, bottom=0.16, left=0.12, right=0.88)

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

    # Larger, wider canvas — less vertically stretched than a near-square figure
    fig, ax = plt.subplots(figsize=(6.6, 4.8))
    max_r = max(max(abs(x) for x in xs), max(ys), 50) * 1.15
    for ang in (-45, 45):
        r = math.radians(ang)
        ax.plot(
            [0, max_r * math.sin(r)],
            [0, max_r * math.cos(r)],
            color=MUTED,
            lw=1.2,
            alpha=0.75,
        )
    theta = np.linspace(math.radians(-45), math.radians(45), 80)
    for radius in (150, 200, 250, 300, 350, 400):
        if radius > max_r:
            continue
        ax.plot(
            radius * np.sin(theta),
            radius * np.cos(theta),
            color=MUTED,
            lw=0.6,
            alpha=0.4,
        )

    # Home plate marker at origin
    from matplotlib.patches import Polygon

    plate_ft = 0.7
    ax.add_patch(
        Polygon(
            [
                (-plate_ft, 0),
                (-plate_ft, plate_ft * 0.6),
                (0, plate_ft * 1.2),
                (plate_ft, plate_ft * 0.6),
                (plate_ft, 0),
            ],
            closed=True,
            facecolor=NAVY,
            edgecolor=NAVY,
            alpha=0.35,
            zorder=1,
        )
    )

    sc = ax.scatter(xs, ys, c=evs, cmap="YlOrRd", edgecolors=NAVY, s=48, linewidths=0.5, alpha=0.9, zorder=3)
    cbar = fig.colorbar(sc, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label("EV (mph)", fontsize=8)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-max_r, max_r)
    ax.set_ylim(-max_r * 0.05, max_r)
    ax.set_title("Spray chart", fontsize=12)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(True, linestyle=":", alpha=0.3)
    for spine in ax.spines.values():
        spine.set_visible(False)
    data = _png_bytes(fig)
    _close(fig)
    return data


# ---------------------------------------------------------------------------
# HitTrax — zone heatmaps + depth (dark theme)
# ---------------------------------------------------------------------------


def _pitch_xy(c: dict[str, Any]) -> Optional[tuple[float, float]]:
    """
    Pitch location in unit square [0,1]×[0,1] (catcher's view: x left→right, y low→high).

    Prefer HitTrax PBH/PBV — those are the plate-crossing coordinates the 13-zone
    charts are built from. Intersect2/3 are point-of-contact spatial coords and
    are not on the same scale (using them left most dots in empty corners).
    """
    pbh, pbv = c.get("pbh"), c.get("pbv")
    if pbh is not None and pbv is not None:
        # Calibrated from observed HitTrax ranges (~PBH 1.5–7.5, PBV 7.5–20)
        nx = (float(pbh) - 1.0) / 7.0
        ny = (float(pbv) - 7.0) / 14.0
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))
    # Rare fallback: only if Intersect already looks unit-normalized
    x, y = c.get("intersect2"), c.get("intersect3")
    if x is None or y is None:
        return None
    fx, fy = float(x), float(y)
    if 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0:
        return fx, fy
    return None


def _plate_xy(c: dict[str, Any]) -> Optional[tuple[float, float]]:
    """Alias for catcher's-view plate location (pitch path through the zone)."""
    return _pitch_xy(c)


def _assign_zone(x: float, y: float) -> str:
    """
    13-zone HitTrax-style layout on unit square.
    Core 3×3 for x,y in [0.25, 0.75]; four L-shaped outer chase corners.
    """
    lo, hi = 0.25, 0.75
    if lo <= x <= hi and lo <= y <= hi:
        col = 0 if x < lo + (hi - lo) / 3 else (1 if x < lo + 2 * (hi - lo) / 3 else 2)
        row = 0 if y > hi - (hi - lo) / 3 else (1 if y > lo + (hi - lo) / 3 else 2)
        # row 0 = high, 2 = low
        return f"z{row}{col}"
    # Outside the core: quadrant L-zones (covers corners + adjacent edge strips)
    if x < 0.5 and y >= 0.5:
        return "outer_tl"
    if x >= 0.5 and y >= 0.5:
        return "outer_tr"
    if x < 0.5 and y < 0.5:
        return "outer_bl"
    return "outer_br"


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


def _outer_zone_polys() -> list[tuple[str, list[tuple[float, float]]]]:
    """L-shaped outer chase zones that fill the frame around the 3×3 core."""
    return [
        (
            "outer_tl",
            [(0.0, 0.5), (0.0, 1.0), (0.5, 1.0), (0.5, 0.75), (0.25, 0.75), (0.25, 0.5)],
        ),
        (
            "outer_tr",
            [(0.5, 1.0), (1.0, 1.0), (1.0, 0.5), (0.75, 0.5), (0.75, 0.75), (0.5, 0.75)],
        ),
        (
            "outer_bl",
            [(0.0, 0.0), (0.0, 0.5), (0.25, 0.5), (0.25, 0.25), (0.5, 0.25), (0.5, 0.0)],
        ),
        (
            "outer_br",
            [(0.5, 0.0), (0.5, 0.25), (0.75, 0.25), (0.75, 0.5), (1.0, 0.5), (1.0, 0.0)],
        ),
    ]


def _all_zone_ids() -> list[str]:
    return [z for z, _ in _outer_zone_polys()] + [z for z, *_ in _core_rects()]


def _draw_home_plate_unit(ax, *, zorder: int = 4) -> None:
    """Home plate in unit-square plate coords (catcher's view, tip toward catcher / bottom)."""
    from matplotlib.patches import Polygon

    # Centered under the strike zone core
    cx, top, tip = 0.5, 0.22, 0.02
    half = 0.08
    ax.add_patch(
        Polygon(
            [
                (cx - half, top),
                (cx - half, top - 0.06),
                (cx, tip),
                (cx + half, top - 0.06),
                (cx + half, top),
            ],
            closed=True,
            facecolor="white",
            edgecolor="white",
            alpha=0.45,
            linewidth=1.0,
            zorder=zorder,
        )
    )


def _zone_heatmap(contacts: list[dict[str, Any]], metric: str, title: str, unit: str) -> Optional[bytes]:
    buckets: dict[str, list[float]] = {z: [] for z in _all_zone_ids()}
    scatter: list[tuple[float, float, float]] = []
    for c in contacts:
        xy = _pitch_xy(c)
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
    from matplotlib.patches import Polygon, Rectangle

    avgs = {k: (sum(v) / len(v) if v else None) for k, v in buckets.items()}
    present = [a for a in avgs.values() if a is not None]
    if not present:
        return None
    vmin, vmax = min(present), max(present)
    if abs(vmax - vmin) < 1e-6:
        vmax = vmin + 1.0
    cmap = plt.get_cmap("coolwarm")
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

    fig, ax = plt.subplots(figsize=(4.2, 4.4), facecolor=DARK_BG)
    ax.set_facecolor(DARK_BG)
    total_n = len(scatter)

    def _zone_color(zid: str) -> str:
        avg = avgs.get(zid)
        if avg is None:
            return "#4a4a4a"
        return cmap(norm(avg))

    def _zone_label(zid: str, cx: float, cy: float) -> None:
        avg = avgs.get(zid)
        if avg is None:
            return
        n = len(buckets.get(zid) or [])
        ax.text(
            cx,
            cy,
            f"{avg:.0f} {unit}\n{n}/{total_n}",
            ha="center",
            va="center",
            color="white",
            fontsize=7,
            fontweight="bold",
            zorder=4,
        )

    for zid, verts in _outer_zone_polys():
        ax.add_patch(
            Polygon(
                verts,
                closed=True,
                facecolor=_zone_color(zid),
                edgecolor="white",
                linewidth=0.8,
                alpha=0.9,
                zorder=1,
            )
        )
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        _zone_label(zid, sum(xs) / len(xs), sum(ys) / len(ys))

    for zid, x0, y0, w, h in _core_rects():
        ax.add_patch(
            Rectangle(
                (x0, y0),
                w,
                h,
                facecolor=_zone_color(zid),
                edgecolor="white",
                linewidth=0.8,
                alpha=0.9,
                zorder=2,
            )
        )
        _zone_label(zid, x0 + w / 2, y0 + h / 2)

    _draw_home_plate_unit(ax, zorder=6)

    # Contact overlay (clipped to the unit frame)
    xs = [p[0] for p in scatter]
    ys = [p[1] for p in scatter]
    ax.scatter(
        xs,
        ys,
        c="#60a5fa",
        s=12,
        alpha=0.55,
        edgecolors="none",
        zorder=5,
        clip_on=True,
    )

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


def _plate_vertical_chart(contacts: list[dict[str, Any]]) -> Optional[bytes]:
    """
    Catcher's view: plate lateral × height, EV-colored.
    Inch markers on both axes (plate width ≈ 17", zone height ≈ 18–42").
    """
    pts: list[tuple[float, float, float]] = []
    for c in contacts:
        xy = _plate_xy(c)
        ev = c.get("ev")
        if xy is None or ev is None:
            continue
        # Convert unit plate coords → inches (catcher's view)
        lat_in = (xy[0] - 0.5) * 17.0
        # Unit y 0.25→18" (bottom zone), 0.75→42" (top zone)
        height_in = 18.0 + (xy[1] - 0.25) * 48.0
        pts.append((lat_in, height_in, float(ev)))
    if not pts:
        return None

    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Polygon

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    evs = [p[2] for p in pts]

    fig, ax = plt.subplots(figsize=(4.4, 4.8), facecolor=DARK_BG)
    ax.set_facecolor(DARK_BG)

    # Strike zone guide in inches
    ax.add_patch(
        Rectangle(
            (-8.5, 18.0),
            17.0,
            24.0,
            fill=False,
            edgecolor="white",
            linewidth=1.2,
            linestyle="--",
            alpha=0.7,
            zorder=2,
        )
    )
    # Home plate under the zone (catcher's view, tip toward catcher / down)
    ax.add_patch(
        Polygon(
            [
                (-8.5, 8.0),
                (-8.5, 2.0),
                (0.0, -2.0),
                (8.5, 2.0),
                (8.5, 8.0),
            ],
            closed=True,
            facecolor="white",
            edgecolor="white",
            alpha=0.35,
            linewidth=1.0,
            zorder=3,
        )
    )
    sc = ax.scatter(
        xs, ys, c=evs, cmap="YlOrRd", s=36, edgecolors="white", linewidths=0.35, alpha=0.9, zorder=4
    )
    cbar = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.yaxis.set_tick_params(color="white", labelcolor="white")
    cbar.outline.set_edgecolor("white")
    cbar.set_label("EV (mph)", color="white", fontsize=8)

    # Inch markers
    y_lo = min(-4.0, min(ys) - 4)
    y_hi = max(48.0, max(ys) + 4)
    ax.set_xlim(-20, 20)
    ax.set_ylim(y_lo, y_hi)
    ax.set_aspect("equal")
    x_ticks = [-17, -8.5, 0, 8.5, 17]
    y_ticks = [0, 18, 30, 42]
    ax.set_xticks(x_ticks)
    ax.set_xticklabels([f"{t:g}\"" for t in x_ticks], color="white", fontsize=7)
    ax.set_yticks(y_ticks)
    ax.set_yticklabels([f"{t:g} IN" for t in y_ticks], color="white", fontsize=7)
    ax.tick_params(colors="white", length=3)
    ax.grid(True, which="major", color="white", alpha=0.18, linewidth=0.6, zorder=1)
    for spine in ax.spines.values():
        spine.set_color("white")
    ax.set_title("Contact location (vertical)", color="white", fontsize=11, pad=8)
    ax.set_xlabel("← Glove   Plate   Arm →", color="#9ca3af", fontsize=7, labelpad=4)
    ax.set_ylabel("Height", color="#9ca3af", fontsize=7)
    fig.patch.set_facecolor(DARK_BG)
    data = _png_bytes(fig, facecolor=DARK_BG)
    _close(fig)
    return data


def _plate_horizontal_chart(contacts: list[dict[str, Any]]) -> Optional[bytes]:
    """
    EV by depth of contact: lateral × depth inches, with inch markers like HitTrax.
    +depth = out in front of the plate (toward pitcher).
    """
    pts: list[tuple[float, float, float]] = []
    for c in contacts:
        xy = _plate_xy(c)
        i1 = c.get("intersect1")
        ev = c.get("ev")
        if xy is None or i1 is None or ev is None:
            continue
        # x: inches across plate (~17" wide), y: depth inches
        lat_in = (xy[0] - 0.5) * 17.0
        depth_in = float(i1) * M_TO_IN
        pts.append((lat_in, depth_in, float(ev)))
    if not pts:
        return None

    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    evs = [p[2] for p in pts]

    fig, ax = plt.subplots(figsize=(4.8, 4.6), facecolor=DARK_BG)
    ax.set_facecolor(DARK_BG)

    # Top-down home plate (tip toward pitcher = +depth / up the page)
    plate = Polygon(
        [(-8.5, 0), (-8.5, 8.5), (0, 17), (8.5, 8.5), (8.5, 0)],
        closed=True,
        facecolor="#9ca3af",
        edgecolor="white",
        alpha=0.35,
        linewidth=1.0,
        zorder=1,
    )
    ax.add_patch(plate)

    sc = ax.scatter(
        xs, ys, c=evs, cmap="turbo", s=36, edgecolors="white", linewidths=0.35, alpha=0.9, zorder=4
    )
    cbar = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.06)
    cbar.ax.yaxis.set_tick_params(color="white", labelcolor="white")
    cbar.outline.set_edgecolor("white")
    cbar.set_label("EV (mph)", color="white", fontsize=8)

    depth_ticks = [18, 6, 0, -6, -18]
    y_pad = max(abs(min(ys)), abs(max(ys)), 20) + 4
    ax.set_xlim(-18, 18)
    ax.set_ylim(-y_pad, y_pad)
    ax.set_xticks([-17, -8.5, 0, 8.5, 17])
    ax.set_xticklabels(["-17\"", "-8.5\"", "0\"", "8.5\"", "17\""], color="white", fontsize=7)
    ax.set_yticks(depth_ticks)
    ax.set_yticklabels([f"{t:+d} IN" if t != 0 else "0 IN" for t in depth_ticks], color="white", fontsize=7)
    ax.tick_params(colors="white", length=3)
    for y in depth_ticks:
        ax.axhline(y, color="white", lw=0.6, alpha=0.28, zorder=2)
    ax.axhline(0, color="white", lw=0.9, alpha=0.45, zorder=2)

    # Mean EV per depth band (right-side callouts, matching example style)
    bands = [(18, 6), (6, 0), (0, -6), (-6, -18)]
    x_right = ax.get_xlim()[1]
    for hi, lo in bands:
        band_evs = [ev for (_, y, ev) in pts if lo <= y <= hi]
        if not band_evs:
            continue
        mid = (hi + lo) / 2
        ax.text(
            x_right - 0.4,
            mid,
            f"{statistics.mean(band_evs):.1f} mph",
            ha="right",
            va="center",
            color="white",
            fontsize=7,
            alpha=0.9,
            zorder=5,
        )

    for spine in ax.spines.values():
        spine.set_color("white")
    ax.set_title("EV by Depth of Contact", color="white", fontsize=11, pad=8)
    ax.set_xlabel("← Glove   Plate   Arm →", color="#9ca3af", fontsize=7, labelpad=4)
    ax.set_ylabel("Depth (out front +, deep −)", color="#9ca3af", fontsize=7)
    fig.patch.set_facecolor(DARK_BG)
    data = _png_bytes(fig, facecolor=DARK_BG)
    _close(fig)
    return data


def build_hittrax_chart_images(contacts: list[dict[str, Any]]) -> dict[str, bytes]:
    """
    Keys: ev_la, spray, zone_ev, zone_la, plate_vert, plate_horiz
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
        ("plate_vert", lambda: _plate_vertical_chart(contacts)),
        ("plate_horiz", lambda: _plate_horizontal_chart(contacts)),
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
                "m/s",
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
