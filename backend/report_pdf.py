"""Generate HEAT assessment draft PDFs with ReportLab."""
from __future__ import annotations

import os
import re
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape

import report_charts
import report_wellness
from report_metrics import (
    BLAST_BAT_LABELS,
    BLAST_BAT_ORDER,
    HITTRAX_FIELD_ORDER,
    HITTRAX_ZONE_ORDER,
    blast_group_stats,
    hittrax_breakdown_value,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Flowable,
    Image,
    KeepInFrame,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfgen.canvas import Canvas

GREEN = colors.HexColor("#1a7f37")
RED = colors.HexColor("#cf222e")
MUTED = colors.HexColor("#57606a")
DELTA_POS_BG = colors.HexColor("#c6efce")
DELTA_NEG_BG = colors.HexColor("#ffc7ce")
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "rbi_heat_logo.png"
LOGO_WIDTH = 1.35 * inch
LOGO_COL_WIDTH = 1.9 * inch
PAGE_CONTENT_WIDTH = 7.1 * inch
TITLE_COL_WIDTH = PAGE_CONTENT_WIDTH - LOGO_COL_WIDTH

# Best of Day: KPI cards by test (no source-brand names). Hop L/R peak force
# omitted until those columns exist on VALD_FD_HJ.
VALD_TEST_SECTIONS: list[dict[str, Any]] = [
    {
        "title": "Isometric Mid-Thigh Pull",
        "metrics": [
            ("Peak Force / BM", "imtp_peak_force_bm", "N/kg"),
            ("Peak Force", ("imtp_peak_force_l", "imtp_peak_force_r"), "N"),
            ("RFD 150ms", "imtp_rfd_150", "N/s"),
            ("RFD 150ms", ("imtp_rfd_150_l", "imtp_rfd_150_r"), "N/s"),
        ],
    },
    {
        "title": "Hop Test",
        "metrics": [
            ("Best RSI", "hj_best_rsi", "m/s"),
            ("Mean RSI", "hj_mean_rsi", "m/s"),
            ("Best Contact Time", "hj_best_contact_time", "ms"),
            ("Mean Contact Time", "hj_mean_contact_time", "ms"),
            ("Mean Jump Height", "hj_mean_jump_height", "cm"),
            ("Best Jump Height", "hj_best_jump_height", "cm"),
            ("Best Peak Force", "hj_best_peak_force", "N"),
            ("Mean Peak Force", "hj_mean_peak_force", "N"),
        ],
    },
    {
        "title": "Countermovement Jump",
        "metrics": [
            ("Rel. Peak Landing Force", "cmj_rel_landing_force", "N/cm"),
            ("Jump Height", "cmj_jump_height_ft", "cm"),
            ("RSI-modified", "cmj_rsi_mod", "m/s"),
            ("Concentric Duration", "cmj_conc_duration", "ms"),
            ("Conc. Impulse", "cmj_conc_impulse", "N·s"),
            ("Conc. Impulse", ("cmj_conc_impulse_l", "cmj_conc_impulse_r"), "N·s"),
            ("Ecc. Accel. Phase", "cmj_ecc_accel_phase", "s"),
            ("Ecc. Braking RFD", "cmj_ecc_braking_rfd", "N/s"),
        ],
    },
    {
        "title": "Squat Jump",
        "metrics": [
            ("Jump Height", "sj_jump_height_ft", "cm"),
            ("Rel. Peak Landing Force", "sj_rel_landing_force", "N/cm"),
            ("Conc. RFD", "sj_conc_rfd", "N/s"),
            ("Conc. RFD", ("sj_conc_rfd_l", "sj_conc_rfd_r"), "N/s"),
            ("Landing Impulse", ("sj_landing_impulse_l", "sj_landing_impulse_r"), "N·s"),
        ],
    },
]


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return s or "player"


def _fmt(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:.1f}"
    return str(val)


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _assessment_type_label(current: dict[str, Any]) -> str:
    raw = (current.get("assessment_type") or "").strip().lower()
    if raw == "retest":
        n = current.get("retest_number")
        if isinstance(n, int) and n >= 1:
            return f"{_ordinal(n)} Retest"
        return "Retest"
    if raw:
        return raw.capitalize()
    return ""


def _previous_label(current: dict[str, Any]) -> str:
    """On a 1st retest the stored previous visit is the initial assessment."""
    if (current.get("assessment_type") or "").strip().lower() != "retest":
        return "Previous"
    n = current.get("retest_number")
    if n == 1 or n == "1":
        return "Initial"
    return "Previous"


def _inline_markdown_to_rl(text: str) -> str:
    """
    Convert a small Markdown subset to ReportLab Paragraph markup.

    Supported inline: **bold**, *italic*, ***both***, `code`, and the
    underscore equivalents. Escapes XML first so raw <>& stay safe.
    """
    s = escape(text or "")
    # Bold+italic first so nested markers are not partially consumed.
    s = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", s)
    s = re.sub(r"___(.+?)___", r"<b><i>\1</i></b>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", s)
    s = re.sub(
        r"`([^`]+)`",
        lambda m: (
            '<font face="Courier" size="8">'
            + m.group(1)
            + "</font>"
        ),
        s,
    )
    return s


def _notes_flowables(
    notes_text: str,
    styles,
    *,
    font_size: float = 9,
    leading: float = 12,
    heading_as_title: bool = False,
) -> list:
    """
    Turn assessment notes into ReportLab flowables.

    Supports:
      - blank-line paragraphs
      - # / ## / ### headings
      - - or * bullets
      - 1. numbered lists
      - inline bold / italic / code (see _inline_markdown_to_rl)
    Plain text still works unchanged.
    """
    suffix = f"{font_size:g}"
    body = ParagraphStyle(
        f"CoachNotes{suffix}",
        parent=styles["Normal"],
        fontSize=font_size,
        leading=leading,
        textColor=MUTED,
        spaceBefore=1,
        spaceAfter=2,
    )
    bullet = ParagraphStyle(
        f"CoachNotesBullet{suffix}",
        parent=body,
        leftIndent=12,
        firstLineIndent=-9,
        spaceBefore=0.5,
        spaceAfter=0.5,
    )
    heading = ParagraphStyle(
        f"CoachNotesHeading{suffix}{'Title' if heading_as_title else ''}",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=8 if heading_as_title else font_size + 1,
        leading=11 if heading_as_title else leading + 1,
        textColor=colors.HexColor("#0b3d5c"),
        spaceBefore=4,
        spaceAfter=2,
    )

    flow: list = []
    lines = notes_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraph_buf: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_buf
        if not paragraph_buf:
            return
        text = " ".join(part.strip() for part in paragraph_buf if part.strip())
        paragraph_buf = []
        if text:
            flow.append(Paragraph(_inline_markdown_to_rl(text), body))

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        number_match = re.match(r"^(\d+)[.)]\s+(.+)$", stripped)

        if heading_match:
            flush_paragraph()
            if heading_as_title:
                style = heading
            else:
                level = len(heading_match.group(1))
                size = {1: 11, 2: 10, 3: 9}[level]
                style = ParagraphStyle(
                    f"CoachNotesH{level}",
                    parent=heading,
                    fontSize=size,
                    leading=size + 3,
                )
            flow.append(
                Paragraph(
                    f"<b>{_inline_markdown_to_rl(heading_match.group(2))}</b>",
                    style,
                )
            )
            continue

        if bullet_match:
            flush_paragraph()
            flow.append(
                Paragraph(
                    f"• {_inline_markdown_to_rl(bullet_match.group(1))}",
                    bullet,
                )
            )
            continue

        if number_match:
            flush_paragraph()
            flow.append(
                Paragraph(
                    f"{number_match.group(1)}. "
                    f"{_inline_markdown_to_rl(number_match.group(2))}",
                    bullet,
                )
            )
            continue

        paragraph_buf.append(stripped)

    flush_paragraph()
    return flow


def _delta_values(curr: Optional[float], other: Optional[float]) -> tuple[str, Optional[str]]:
    """Return (display text, color hex or None for neutral)."""
    if curr is None or other is None:
        return "—", None
    d = curr - other
    sign = "+" if d >= 0 else ""
    text = f"{sign}{d:.1f}"
    if d > 0:
        return text, "#1a7f37"
    if d < 0:
        return text, "#cf222e"
    return text, None


def _delta_para(curr: Optional[float], other: Optional[float], style: ParagraphStyle) -> Paragraph:
    text, color = _delta_values(curr, other)
    if color:
        return Paragraph(f'<font color="{color}"><b>{text}</b></font>', style)
    return Paragraph(text, style)


def _date_only(val: Any) -> str:
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, str) and val:
        return val[:10]
    return "—"


def _short_date(val: Any) -> str:
    """Compact date for wide Blast headers (e.g. 5/3/26)."""
    if isinstance(val, datetime):
        return f"{val.month}/{val.day}/{str(val.year)[-2:]}"
    if isinstance(val, str) and val:
        try:
            parsed = datetime.strptime(val[:10], "%Y-%m-%d")
            return f"{parsed.month}/{parsed.day}/{str(parsed.year)[-2:]}"
        except ValueError:
            return val[:10]
    return "Current"


def _blast_side_by_side_table(
    *,
    current_date: Any,
    current_blast: dict[str, Any],
    previous_blast: dict[str, Any],
    baseline_blast: dict[str, Any],
    has_previous: bool,
    has_baseline: bool,
    section_style: ParagraphStyle,
    cell_style: ParagraphStyle,
    previous_label: str = "Previous",
) -> list:
    """
    One Swing Metrics table with all bat groups side-by-side.

    Layout matches the HEAT hand-built reports:
      Metric | Game Bat (date / Δ prev / Δ init) | Handle Load | …
    """
    bat_keys = list(BLAST_BAT_ORDER)
    cols_per_bat = 1 + (1 if has_previous else 0) + (1 if has_baseline else 0)
    metric_defs = [
        ("Peak Bat Speed (mph)", "peak_bat_speed", True),
        ("Average Bat Speed (mph)", "avg_bat_speed", True),
        ("SD Bat Speed (mph)", "sd_bat_speed", False),
        ("Average Attack Angle (°)", "avg_attack_angle", False),
        ("SD Attack Angle (°)", "sd_attack_angle", False),
    ]

    header_style = ParagraphStyle(
        "BlastBatHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=9,
        alignment=1,
        textColor=colors.HexColor("#111521"),
    )
    sub_header_style = ParagraphStyle(
        "BlastSubHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        fontSize=6,
        leading=7,
        alignment=1,
        textColor=colors.HexColor("#111521"),
    )
    data_style = ParagraphStyle(
        "BlastData",
        parent=cell_style,
        fontSize=7,
        leading=8,
        alignment=1,
    )
    metric_style = ParagraphStyle(
        "BlastMetric",
        parent=cell_style,
        fontName="Helvetica",
        fontSize=7,
        leading=8,
        alignment=0,
    )

    date_label = _short_date(current_date)
    # Two header rows: bat group spans, then date / delta labels.
    top = [""]
    sub = [""]
    for key in bat_keys:
        top.append(Paragraph(BLAST_BAT_LABELS.get(key, key), header_style))
        top.extend([""] * (cols_per_bat - 1))
        sub.append(Paragraph(escape(date_label), sub_header_style))
        if has_previous:
            sub.append(
                Paragraph(f"{escape(previous_label)} vs.<br/>Current", sub_header_style)
            )
        if has_baseline:
            sub.append(Paragraph("Initial vs.<br/>Current", sub_header_style))

    data: list[list[Any]] = [top, sub]
    # Track delta cells that need green/red fills: (row_idx, col_idx, sign)
    colored_deltas: list[tuple[int, int, int]] = []

    for label, field, colorize in metric_defs:
        row: list[Any] = [Paragraph(f"{escape(label)}:", metric_style)]
        for key in bat_keys:
            curr_g = blast_group_stats(current_blast, key)
            prev_g = blast_group_stats(previous_blast, key) if has_previous else {}
            base_g = blast_group_stats(baseline_blast, key) if has_baseline else {}
            curr = curr_g.get(field)
            row.append(Paragraph(_fmt(curr), data_style))
            if has_previous:
                prev_v = prev_g.get(field)
                text, _color = _delta_values(curr, prev_v)
                row.append(Paragraph(escape(text), data_style))
                if colorize and curr is not None and prev_v is not None:
                    d = curr - prev_v
                    if d != 0:
                        colored_deltas.append((len(data), len(row) - 1, 1 if d > 0 else -1))
            if has_baseline:
                base_v = base_g.get(field)
                text, _color = _delta_values(curr, base_v)
                row.append(Paragraph(escape(text), data_style))
                if colorize and curr is not None and base_v is not None:
                    d = curr - base_v
                    if d != 0:
                        colored_deltas.append((len(data), len(row) - 1, 1 if d > 0 else -1))
        data.append(row)

    page_w = 8.5 * inch - 1.4 * inch  # letter minus default margins used by doc
    metric_w = 1.28 * inch
    bat_w = (page_w - metric_w) / max(len(bat_keys), 1)
    col_w = [metric_w]
    for _ in bat_keys:
        share = bat_w / cols_per_bat
        col_w.extend([share] * cols_per_bat)

    table = Table(data, colWidths=col_w, repeatRows=2)
    style_cmds: list = [
        ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#fcba39")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#111521")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        (
            "ROWBACKGROUNDS",
            (0, 2),
            (-1, -1),
            [colors.white, colors.HexColor("#f7f8fa")],
        ),
        # Metric label column spans both header rows
        ("SPAN", (0, 0), (0, 1)),
    ]
    for i, _key in enumerate(bat_keys):
        start = 1 + i * cols_per_bat
        end = start + cols_per_bat - 1
        if cols_per_bat > 1:
            style_cmds.append(("SPAN", (start, 0), (end, 0)))
        # Vertical rule between bat groups
        if i > 0:
            style_cmds.append(
                ("LINEBEFORE", (start, 0), (start, -1), 1.0, colors.HexColor("#111521"))
            )

    for r_i, c_i, sign in colored_deltas:
        bg = DELTA_POS_BG if sign > 0 else DELTA_NEG_BG
        style_cmds.append(("BACKGROUND", (c_i, r_i), (c_i, r_i), bg))

    table.setStyle(TableStyle(style_cmds))
    return [table]


def _hittrax_profile_table(
    *,
    current_ht: dict[str, Any],
    previous_ht: dict[str, Any],
    has_previous: bool,
    cell_style: ParagraphStyle,
    previous_label: str = "Previous",
    baseline_ht: Optional[dict[str, Any]] = None,
    has_baseline: bool = False,
) -> Table:
    """Metric / Current / Previous (or Initial) / Change, plus Initial on later retests."""
    rows_def = [
        ("Peak EV (mph)", "peak_ev"),
        ("Avg EV (mph)", "avg_ev"),
        ("90th EV (mph)", "p90_ev"),
        ("Avg Launch Angle (°)", "avg_launch_angle"),
        ("Avg LA (Hard Hit) (°)", "avg_la_hard_hit"),
        ("Avg EV (LA 5–15°) (mph)", "avg_ev_ideal_la"),
        ("Avg Distance (ft)", "avg_distance"),
        ("Contacts", "swing_count"),
    ]
    header_style = ParagraphStyle(
        "HtProfileHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        alignment=1,
        textColor=colors.white,
    )
    metric_style = ParagraphStyle(
        "HtProfileMetric",
        parent=cell_style,
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        alignment=0,
        textColor=colors.HexColor("#111521"),
    )
    data_style = ParagraphStyle(
        "HtProfileData",
        parent=cell_style,
        fontSize=8,
        leading=11,
        alignment=1,
        textColor=colors.HexColor("#111521"),
    )
    show_base = bool(has_baseline)
    header = [
        Paragraph("Metric", header_style),
        Paragraph("Current", header_style),
    ]
    if has_previous:
        header.append(Paragraph(escape(previous_label), header_style))
        header.append(
            Paragraph(
                "Δ Init" if previous_label == "Initial" else "Δ Prev",
                header_style,
            )
        )
    if show_base:
        header.append(Paragraph("Initial", header_style))
        header.append(Paragraph("Δ Init", header_style))
    data: list[list[Any]] = [header]
    la_neutral = {"avg_launch_angle", "avg_la_hard_hit"}
    base_ht = baseline_ht or {}
    for label, field in rows_def:
        curr = current_ht.get(field)
        prev = previous_ht.get(field) if has_previous else None
        base = base_ht.get(field) if show_base else None
        row: list[Any] = [
            Paragraph(escape(label), metric_style),
            Paragraph(_fmt(curr), data_style),
        ]
        if has_previous:
            row.append(Paragraph(_fmt(prev), data_style))
            row.append(
                Paragraph(
                    _kpi_delta_markup(curr, prev, neutral=field in la_neutral)
                    or "—",
                    data_style,
                )
            )
        if show_base:
            row.append(Paragraph(_fmt(base), data_style))
            row.append(
                Paragraph(
                    _kpi_delta_markup(curr, base, neutral=field in la_neutral)
                    or "—",
                    data_style,
                )
            )
        data.append(row)
    n_cols = len(header)
    metric_w = 1.9 * inch if show_base else 2.5 * inch
    rest = PAGE_CONTENT_WIDTH - metric_w
    other_w = rest / max(n_cols - 1, 1)
    table = Table(data, colWidths=[metric_w] + [other_w] * (n_cols - 1))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f5f7fa")],
                ),
            ]
        )
    )
    return table


def _hittrax_location_breakdown_table(
    *,
    current_date: Any,
    current_breakdown: dict[str, Any],
    previous_breakdown: dict[str, Any],
    baseline_breakdown: dict[str, Any],
    has_previous: bool,
    has_baseline: bool,
    section_style: ParagraphStyle,
    cell_style: ParagraphStyle,
    previous_label: str = "Previous",
) -> list:
    """
    Avg EV / LA / distance by field third and by zone, with Δ prev / Δ initial.

    Matches the example-PDF location comparison layout.
    """
    if not current_breakdown and not previous_breakdown and not baseline_breakdown:
        return []

    metric_keys = (
        ("avg_ev", "Average Exit Velocity (mph)"),
        ("avg_launch_angle", "Average Launch Angle (°)"),
        ("avg_distance", "Average Distance (ft)"),
    )
    cols_per_metric = 1 + (1 if has_previous else 0) + (1 if has_baseline else 0)

    header_style = ParagraphStyle(
        "HtLocHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=8,
        alignment=1,
        textColor=colors.white,
    )
    sub_header_style = ParagraphStyle(
        "HtLocSubHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        fontSize=5.5,
        leading=6.5,
        alignment=1,
        textColor=colors.HexColor("#111521"),
    )
    data_style = ParagraphStyle(
        "HtLocData",
        parent=cell_style,
        fontSize=7,
        leading=8,
        alignment=1,
    )
    row_style = ParagraphStyle(
        "HtLocRow",
        parent=cell_style,
        fontSize=7,
        leading=8,
        alignment=0,
    )
    section_cell_style = ParagraphStyle(
        "HtLocSection",
        parent=cell_style,
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=8,
        alignment=1,
        textColor=colors.HexColor("#0b3d5c"),
    )

    date_label = _short_date(current_date)
    top: list[Any] = ["", ""]
    sub: list[Any] = ["", ""]
    for _key, title in metric_keys:
        top.append(Paragraph(escape(title), header_style))
        top.extend([""] * (cols_per_metric - 1))
        sub.append(Paragraph(escape(date_label), sub_header_style))
        if has_previous:
            sub.append(
                Paragraph(f"{escape(previous_label)} vs.<br/>Current", sub_header_style)
            )
        if has_baseline:
            sub.append(Paragraph("Initial vs.<br/>Current", sub_header_style))

    data: list[list[Any]] = [top, sub]
    colored_deltas: list[tuple[int, int, int]] = []

    sections = (
        ("By Field", "by_field", HITTRAX_FIELD_ORDER),
        ("By Zone", "by_zone", HITTRAX_ZONE_ORDER),
    )
    section_spans: list[tuple[int, int]] = []  # (start_row, end_row) inclusive

    for section_label, section_key, rows in sections:
        start_row = len(data)
        for i, (row_key, row_label) in enumerate(rows):
            section_cell = (
                Paragraph(escape(section_label), section_cell_style) if i == 0 else ""
            )
            row: list[Any] = [
                section_cell,
                Paragraph(f"{escape(row_label)}:", row_style),
            ]
            for metric_field, _title in metric_keys:
                curr = hittrax_breakdown_value(
                    current_breakdown, section_key, row_key, metric_field
                )
                row.append(Paragraph(_fmt(curr), data_style))
                if has_previous:
                    prev_v = hittrax_breakdown_value(
                        previous_breakdown, section_key, row_key, metric_field
                    )
                    text, _color = _delta_values(curr, prev_v)
                    row.append(Paragraph(escape(text), data_style))
                    if curr is not None and prev_v is not None and curr != prev_v:
                        colored_deltas.append(
                            (len(data), len(row) - 1, 1 if curr > prev_v else -1)
                        )
                if has_baseline:
                    base_v = hittrax_breakdown_value(
                        baseline_breakdown, section_key, row_key, metric_field
                    )
                    text, _color = _delta_values(curr, base_v)
                    row.append(Paragraph(escape(text), data_style))
                    if curr is not None and base_v is not None and curr != base_v:
                        colored_deltas.append(
                            (len(data), len(row) - 1, 1 if curr > base_v else -1)
                        )
            data.append(row)
        end_row = len(data) - 1
        if end_row > start_row:
            section_spans.append((start_row, end_row))

    page_w = 8.5 * inch - 1.4 * inch
    section_w = 0.55 * inch
    label_w = 0.85 * inch
    metric_w = (page_w - section_w - label_w) / max(len(metric_keys), 1)
    col_w = [section_w, label_w]
    for _ in metric_keys:
        share = metric_w / cols_per_metric
        col_w.extend([share] * cols_per_metric)

    table = Table(data, colWidths=col_w, repeatRows=2)
    style_cmds: list = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d5c")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#d9e6f2")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#111521")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        (
            "ROWBACKGROUNDS",
            (0, 2),
            (-1, -1),
            [colors.white, colors.HexColor("#f7f8fa")],
        ),
        ("SPAN", (0, 0), (1, 0)),
        ("SPAN", (0, 1), (1, 1)),
        ("BACKGROUND", (0, 2), (0, -1), colors.HexColor("#eef3f8")),
    ]
    for i, (_k, _t) in enumerate(metric_keys):
        start = 2 + i * cols_per_metric
        end = start + cols_per_metric - 1
        if cols_per_metric > 1:
            style_cmds.append(("SPAN", (start, 0), (end, 0)))
        if i > 0:
            style_cmds.append(
                ("LINEBEFORE", (start, 0), (start, -1), 1.0, colors.HexColor("#111521"))
            )
    for start_row, end_row in section_spans:
        style_cmds.append(("SPAN", (0, start_row), (0, end_row)))
        # Divider between By Field and By Zone blocks
        if start_row > 2:
            style_cmds.append(
                (
                    "LINEABOVE",
                    (0, start_row),
                    (-1, start_row),
                    1.0,
                    colors.HexColor("#111521"),
                )
            )

    for r_i, c_i, sign in colored_deltas:
        bg = DELTA_POS_BG if sign > 0 else DELTA_NEG_BG
        style_cmds.append(("BACKGROUND", (c_i, r_i), (c_i, r_i), bg))

    table.setStyle(TableStyle(style_cmds))
    return [table]


def _comparison_table(
    title: str,
    header_bg: colors.Color,
    header_fg: colors.Color,
    metric_rows: list[tuple[str, Optional[float], Optional[float], Optional[float]]],
    has_previous: bool,
    has_baseline: bool,
    section_style: ParagraphStyle,
    cell_style: ParagraphStyle,
    definitions: Optional[dict[str, str]] = None,
    units: Optional[dict[str, str]] = None,
    previous_label: str = "Previous",
) -> list:
    """Build a section heading + metric table with colored Δ cells.

    When ``definitions`` maps metric label → description, each Metric cell
    shows the name plus a muted definition underneath (fills table width;
    avoids a separate definitions block). ``units`` maps label → unit shown
    next to the metric name.
    """
    flow: list = [Paragraph(title, section_style)]
    headers = ["Metric", "Current"]
    if has_previous:
        headers.append("Δ Init" if previous_label == "Initial" else "Δ Prev")
    if has_baseline:
        headers.append("Δ Base")

    def_style = None
    if definitions:
        def_style = ParagraphStyle(
            "MetricDefInline",
            parent=cell_style,
            fontName="Helvetica",
            fontSize=7,
            leading=9,
            textColor=MUTED,
            spaceBefore=1,
        )

    data: list[list[Any]] = [headers]
    for label, curr, prev_v, base_v in metric_rows:
        desc = (definitions or {}).get(label, "").strip() if definitions else ""
        unit = (units or {}).get(label, "").strip() if units else ""
        name_html = escape(label)
        if unit:
            name_html = f"{name_html} ({escape(unit)})"
        if desc and def_style is not None:
            # Plain list stacks name + definition in the cell. KeepTogether inside
            # a Table cell reports an effectively infinite height (LayoutError).
            metric_cell = [
                Paragraph(f"<b>{name_html}</b>", cell_style),
                Paragraph(escape(desc), def_style),
            ]
        elif unit:
            metric_cell = Paragraph(name_html, cell_style)
        else:
            metric_cell = label
        row: list[Any] = [metric_cell, _fmt(curr)]
        if has_previous:
            row.append(_delta_para(curr, prev_v, cell_style))
        if has_baseline:
            row.append(_delta_para(curr, base_v, cell_style))
        data.append(row)

    # Wider Metric column when definitions sit inline; use more page width.
    if definitions:
        metric_w = 4.2 * inch
        value_w = 0.95 * inch
        delta_w = 0.85 * inch
    else:
        metric_w = 2.4 * inch
        value_w = 1.3 * inch
        delta_w = 1.1 * inch
    col_w = [metric_w, value_w] + [delta_w] * (len(headers) - 2)
    table = Table(data, colWidths=col_w)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                ("TEXTCOLOR", (0, 0), (-1, 0), header_fg),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f5f7fa")],
                ),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("VALIGN", (0, 1), (0, -1), "TOP" if definitions else "MIDDLE"),
            ]
        )
    )
    flow.append(table)
    return flow


def _image_grid_table(
    images: list,
    col_width: float = 3.5 * inch,
    per_row: int = 2,
    *,
    pad: float = 10,
    bottom_pad: float = 14,
) -> Optional[Table]:
    """ReportLab table of images in rows of up to `per_row`, or None if empty."""
    if not images:
        return None
    rows: list = []
    for i in range(0, len(images), per_row):
        chunk = list(images[i : i + per_row])
        chunk += [""] * (per_row - len(chunk))
        rows.append(chunk)
    table = Table(rows, colWidths=[col_width] * per_row)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), pad),
                ("RIGHTPADDING", (0, 0), (-1, -1), pad),
                ("TOPPADDING", (0, 0), (-1, -1), pad),
                ("BOTTOMPADDING", (0, 0), (-1, -1), bottom_pad),
            ]
        )
    )
    return table


def _distributed_image_row(
    images: list,
    page_width: float = 7.1 * inch,
    *,
    pad: float = 8,
    bottom_pad: float = 6,
) -> Optional[Table]:
    """One row of images centered and evenly spaced across ``page_width``."""
    if not images:
        return None
    n = len(images)
    col_w = page_width / n
    table = Table([list(images)], colWidths=[col_w] * n)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), pad),
                ("RIGHTPADDING", (0, 0), (-1, -1), pad),
                ("TOPPADDING", (0, 0), (-1, -1), pad),
                ("BOTTOMPADDING", (0, 0), (-1, -1), bottom_pad),
            ]
        )
    )
    return table


def _append_image_grid(
    story: list,
    images: list,
    col_width: float = 3.5 * inch,
    per_row: int = 2,
    *,
    pad: float = 10,
    bottom_pad: float = 14,
    after_space: float = 12,
) -> None:
    """Append ReportLab Images in rows of up to `per_row`."""
    table = _image_grid_table(
        images, col_width, per_row, pad=pad, bottom_pad=bottom_pad
    )
    if table is None:
        return
    story.append(table)
    if after_space:
        story.append(Spacer(1, after_space))


def _ht_image(chart_images: dict[str, Any], key: str, width: float, aspect: float):
    png = chart_images.get(key)
    if not png:
        return None
    img = Image(BytesIO(png), width=width, height=width * aspect)
    img.hAlign = "CENTER"
    return img


def _chart_card(
    title: str,
    img,
    caption: str,
    width: float,
    styles,
    *,
    caption_gap: float = 14,
) -> Table:
    """Shaded card: navy title, chart, caption — matches the zone-page mockup."""
    title_style = ParagraphStyle(
        "ZoneCardTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=NAVY,
        alignment=1,
        spaceAfter=4,
    )
    cap_style = ParagraphStyle(
        "ZoneCardCaption",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        textColor=MUTED,
        alignment=0,
        spaceBefore=0,
    )
    bits: list = [Paragraph(escape(title.upper()), title_style), img]
    if caption:
        cap = Paragraph(escape(caption), cap_style)
        cap_w = getattr(img, "drawWidth", None) or (width - 16)
        cap_row = Table([[cap]], colWidths=[cap_w])
        cap_row.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        bits.append(Spacer(1, caption_gap))
        bits.append(cap_row)
    card = Table([[bits]], colWidths=[width])
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f4f6")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return card


LEFT_BLUE = colors.HexColor("#3b82f6")
RIGHT_ORANGE = colors.HexColor("#f97316")
NAVY = colors.HexColor("#0b3d5c")
PHASE_STATUS_COLORS = {
    "strength": (colors.HexColor("#c6efce"), "#1a7f37", "STRENGTH"),
    "monitor": (colors.HexColor("#fff3cd"), "#b45309", "MONITOR"),
    "development": (colors.HexColor("#ffc7ce"), "#cf222e", "DEVELOPMENT"),
}
MECHANICS_SEQUENCE = (
    ("load_phase", "Load Phase"),
    ("load_position", "Load Position"),
    ("stride_phase", "Stride Phase"),
    ("launch_position", "Launch Position"),
    ("impact", "Impact"),
)

# Named PDF pages for notebook preview (`build_pdf(..., pages="mechanics")`).
PDF_PAGE_KEYS = (
    "mechanics",
    "batted_ball",
    "location",
    "contact",
    "flight_spray",
    "best_of_day",
    "other_visuals",
    "wellness",
)

def _dash(val: Any) -> str:
    if val is None:
        return "—"
    text = str(val).strip()
    return text if text else "—"


def _pretty_date(val: Any) -> str:
    if isinstance(val, datetime):
        return val.strftime("%b %d, %Y")
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%b %d, %Y")
        except Exception:
            pass
    if isinstance(val, str) and val:
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d").strftime("%b %d, %Y")
        except ValueError:
            return val[:10]
    return "—"


def _phase_note_entry(raw: Any) -> tuple[str, str]:
    """Return (status, caption). Legacy string values are caption-only."""
    if isinstance(raw, dict):
        status = str(raw.get("status") or "").strip().lower()
        caption = str(
            raw.get("caption") or raw.get("notes") or raw.get("text") or ""
        ).strip()
        return status, caption
    return "", str(raw or "").strip()


def _fit_image(raw: bytes, max_width: float, max_height: float) -> Optional[Image]:
    try:
        image_width, image_height = ImageReader(BytesIO(raw)).getSize()
        scale = min(max_width / image_width, max_height / image_height, 1.0)
        img = Image(
            BytesIO(raw),
            width=image_width * scale,
            height=image_height * scale,
        )
        img.hAlign = "CENTER"
        return img
    except Exception:
        return None


# Portrait well for page-1 phase photos (matches the filled Peele 2026-01-12 cards).
PHASE_PHOTO_WIDTH = 7.1 * inch / 5 - 0.12 * inch
PHASE_PHOTO_HEIGHT = 1.55 * inch


def _cover_jpeg(raw: bytes, width_pt: float, height_pt: float) -> Optional[bytes]:
    """EXIF-correct, center-crop to the frame, return JPEG bytes."""
    try:
        from PIL import Image as PILImage, ImageOps
    except ImportError:
        return None
    try:
        im = PILImage.open(BytesIO(raw))
        im = ImageOps.exif_transpose(im) or im
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
            im.load()
        if im.mode == "P":
            im = im.convert("RGBA")
        if im.mode == "RGBA":
            bg = PILImage.new("RGB", im.size, (243, 244, 246))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        sw, sh = im.size
        if sw <= 0 or sh <= 0 or height_pt <= 0:
            return None
        target = width_pt / height_pt
        src = sw / sh
        if src > target:
            new_w = max(1, int(round(sh * target)))
            left = (sw - new_w) // 2
            im = im.crop((left, 0, left + new_w, sh))
        elif src < target:
            new_h = max(1, int(round(sw / target)))
            top = (sh - new_h) // 2
            im = im.crop((0, top, sw, top + new_h))
        px_w = max(1, int(round(width_pt / inch * 220)))
        px_h = max(1, int(round(height_pt / inch * 220)))
        im = im.resize((px_w, px_h), PILImage.Resampling.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()
    except Exception:
        return None


class _PhasePhotoFrame(Flowable):
    """Fixed-size portrait well so every phase card uses the same photo frame."""

    def __init__(self, raw: Optional[bytes], width: float, height: float):
        Flowable.__init__(self)
        self.frame_w = width
        self.frame_h = height
        self._jpeg = _cover_jpeg(raw, width, height) if raw else None
        self._raw = raw if raw and not self._jpeg else None

    def wrap(self, availWidth, availHeight):
        self.width = self.frame_w
        self.height = self.frame_h
        return self.frame_w, self.frame_h

    def draw(self):
        c = self.canv
        w, h = self.frame_w, self.frame_h
        c.setFillColor(colors.HexColor("#e8eef3"))
        c.setStrokeColor(colors.HexColor("#d0d7de"))
        c.setLineWidth(0.3)
        c.roundRect(0, 0, w, h, 3, fill=1, stroke=1)
        src = self._jpeg or self._raw
        if not src:
            c.setFillColor(MUTED)
            c.setFont("Helvetica-Oblique", 7)
            c.drawCentredString(w / 2, h / 2 - 3, "No photo")
            return
        try:
            c.drawImage(
                ImageReader(BytesIO(src)),
                0,
                0,
                width=w,
                height=h,
                preserveAspectRatio=bool(self._raw),
                anchor="c",
                mask="auto",
            )
        except Exception:
            c.setFillColor(MUTED)
            c.setFont("Helvetica-Oblique", 7)
            c.drawCentredString(w / 2, h / 2 - 3, "—")


def _logo_image(width: float) -> Optional[Image]:
    if not LOGO_PATH.is_file():
        return None
    img = Image(str(LOGO_PATH), width=width, height=width * (918 / 1546))
    img.hAlign = "RIGHT"
    return img


def _page_logo_row(left: Any = None, *, valign: str = "TOP") -> Table:
    """Same top-right logo placement/size as page 1 on every later page."""
    logo = _logo_image(LOGO_WIDTH) or ""
    table = Table(
        [[left if left is not None else "", logo]],
        colWidths=[TITLE_COL_WIDTH, LOGO_COL_WIDTH],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), valign),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _kpi_delta_markup(curr: Any, prev: Any, *, neutral: bool = False) -> str:
    if curr is None or prev is None:
        return ""
    try:
        delta = float(curr) - float(prev)
    except (TypeError, ValueError):
        return ""
    if abs(delta) < 1e-9:
        return '<font color="#57606a">—</font>'
    arrow = "▲" if delta > 0 else "▼"
    color = "#57606a" if neutral else ("#1a7f37" if delta > 0 else "#cf222e")
    if abs(delta - round(delta)) < 1e-6:
        shown = f"{delta:+.0f}"
    else:
        shown = f"{delta:+.1f}"
    return f'<font color="{color}"><b>{shown} {arrow}</b></font>'


def _labeled_kpi_delta(
    curr: Any,
    prev: Any,
    *,
    label: Optional[str] = None,
    neutral: bool = False,
) -> str:
    inner = _kpi_delta_markup(curr, prev, neutral=neutral) or (
        '<font color="#57606a">—</font>'
    )
    if not label:
        return inner
    return f'<font color="#57606a" size="6">{escape(label)}</font> {inner}'


def _kpi_compare_markups(
    curr: Any,
    prev: Any,
    base: Any,
    *,
    has_previous: bool,
    has_baseline: bool,
    previous_label: str = "Previous",
    neutral: bool = False,
) -> list[str]:
    """One or two Δ lines: previous, then initial when both exist."""
    show_two = bool(has_previous and has_baseline)
    lines: list[str] = []
    if has_previous:
        tag = None
        if show_two:
            tag = "Init" if previous_label == "Initial" else "Prev"
        lines.append(
            _labeled_kpi_delta(curr, prev, label=tag, neutral=neutral)
        )
    if has_baseline:
        tag = "Init" if show_two or not has_previous else None
        lines.append(
            _labeled_kpi_delta(curr, base, label=tag, neutral=neutral)
        )
    return lines


def _kpi_pct_markup(curr: Any, prev: Any) -> str:
    """Percent change pill used on left/right split cards."""
    if curr is None or prev is None:
        return ""
    try:
        prev_f = float(prev)
        curr_f = float(curr)
    except (TypeError, ValueError):
        return ""
    if abs(prev_f) < 1e-9:
        return ""
    pct = 100.0 * (curr_f - prev_f) / abs(prev_f)
    if abs(pct) < 0.5:
        return '<font color="#57606a">—</font>'
    arrow = "▲" if pct > 0 else "▼"
    color = "#1a7f37" if pct > 0 else "#cf222e"
    return f'<font color="{color}"><b>{arrow} {abs(pct):.0f}%</b></font>'


def _asymmetry_pct(left: Any, right: Any) -> Optional[float]:
    try:
        l_f = abs(float(left))
        r_f = abs(float(right))
    except (TypeError, ValueError):
        return None
    denom = max(l_f, r_f)
    if denom < 1e-9:
        return None
    return 100.0 * abs(l_f - r_f) / denom


class _AsymBar(Flowable):
    """Horizontal track with a filled share of |L−R| / max(L,R)."""

    def __init__(self, pct: float, width: float, height: float = 5):
        Flowable.__init__(self)
        self.pct = max(0.0, min(float(pct), 100.0))
        self.bar_width = width
        self.bar_height = height

    def wrap(self, availWidth, availHeight):
        self.width = self.bar_width
        self.height = self.bar_height
        return self.width, self.height

    def draw(self):
        c = self.canv
        w, h = self.bar_width, self.bar_height
        c.setFillColor(colors.HexColor("#e5e7eb"))
        c.roundRect(0, 0, w, h, 1.2, fill=1, stroke=0)
        fill = (self.pct / 100.0) * w
        if fill >= 1:
            c.setFillColor(LEFT_BLUE)
            c.roundRect(0, 0, fill, h, 1.2, fill=1, stroke=0)


def _labeled_kpi_pct(
    curr: Any,
    prev: Any,
    *,
    label: Optional[str] = None,
) -> str:
    inner = _kpi_pct_markup(curr, prev) or '<font color="#57606a">—</font>'
    if not label:
        return inner
    return f'<font color="#57606a" size="5.5">{escape(label)}</font> {inner}'


class _KpiShell(Flowable):
    """Shaded KPI card box. Grows to a shared height and centers the inner content."""

    def __init__(self, inner, width: float, *, pad: float = 4):
        Flowable.__init__(self)
        self.inner = inner
        self.box_width = width
        self.pad = pad
        self._forced_height = 0.0
        self._inner_h = 0.0

    def wrap(self, availWidth, availHeight):
        inner_w = max(self.box_width - 2 * self.pad, 16)
        _w, ih = self.inner.wrap(inner_w, 40 * inch)
        self._inner_h = ih
        self.width = self.box_width
        self.height = max(ih + 2 * self.pad, self._forced_height)
        return self.width, self.height

    def match_height(self, height: float) -> None:
        self._forced_height = height
        self.height = max(self.height, height)

    def draw(self):
        c = self.canv
        w, h = self.box_width, self.height
        c.setFillColor(colors.HexColor("#f8fafc"))
        c.setStrokeColor(colors.HexColor("#d0d7de"))
        c.setLineWidth(0.4)
        c.roundRect(0, 0, w, h, 3, fill=1, stroke=1)
        inner_w = max(w - 2 * self.pad, 16)
        _w, ih = self.inner.wrap(inner_w, 40 * inch)
        y = (h - ih) / 2.0
        self.inner.drawOn(c, self.pad, y)


def _kpi_side_stack(
    value: Any,
    unit: str,
    prev: Any,
    has_previous: bool,
    width: float,
    compact: bool,
    *,
    base: Any = None,
    has_baseline: bool = False,
) -> Table:
    """One half of a split card: value, unit, Δ (L/R live on the heading row)."""
    val = ParagraphStyle(
        f"KpiSideVal{int(width)}",
        fontName="Helvetica-Bold",
        fontSize=11 if compact else 14,
        leading=13 if compact else 16,
        textColor=colors.HexColor("#111827"),
        alignment=1,
    )
    unit_style = ParagraphStyle(
        f"KpiSideUnit{int(width)}",
        fontName="Helvetica",
        fontSize=6 if compact else 7,
        leading=7 if compact else 9,
        textColor=MUTED,
        alignment=1,
    )
    delta = ParagraphStyle(
        f"KpiSideDelta{int(width)}",
        fontName="Helvetica",
        fontSize=6.5 if compact else 7,
        leading=8 if compact else 9,
        alignment=1,
    )
    bits = [Paragraph(_fmt(value), val)]
    if unit:
        bits.append(Paragraph(escape(unit), unit_style))
    show_two = has_previous and has_baseline
    if has_previous:
        bits.append(
            Paragraph(
                _labeled_kpi_pct(value, prev, label="P" if show_two else None),
                delta,
            )
        )
    if has_baseline:
        bits.append(
            Paragraph(_labeled_kpi_pct(value, base, label="I"), delta)
        )
    inner = Table([[b] for b in bits], colWidths=[width])
    inner.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    return inner


def _bilateral_kpi_card(
    card: dict[str, Any],
    width: float,
    has_previous: bool,
    compact: bool,
    *,
    has_baseline: bool = False,
) -> Table:
    """Left | Right split card. L/R sit on the heading row above the values."""
    inner_w = max(width, 40)
    gap = 3
    half = max((inner_w - gap) / 2, 16)
    title_style = ParagraphStyle(
        f"KpiBilatTitle{int(width)}",
        fontName="Helvetica",
        fontSize=6.5 if compact else 7,
        leading=8 if compact else 9,
        textColor=MUTED,
        alignment=1,
    )
    letter_style = ParagraphStyle(
        f"KpiBilatLetter{int(width)}",
        fontName="Helvetica-Bold",
        fontSize=6.5 if compact else 7,
        leading=8 if compact else 9,
        alignment=1,
    )
    pct_style = ParagraphStyle(
        f"KpiBilatPct{int(width)}",
        fontName="Helvetica",
        fontSize=5,
        leading=6,
        textColor=MUTED,
        alignment=0,
    )
    letter_w = min(0.16 * inch, inner_w * 0.18)
    title_w = max(inner_w - 2 * letter_w, 20)
    header = Table(
        [[
            Paragraph('<font color="#3b82f6"><b>L</b></font>', letter_style),
            Paragraph(escape((card.get("label") or "").upper()), title_style),
            Paragraph('<font color="#f97316"><b>R</b></font>', letter_style),
        ]],
        colWidths=[letter_w, title_w, letter_w],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    left = _kpi_side_stack(
        card.get("left"),
        card.get("unit") or "",
        card.get("left_prev"),
        has_previous,
        half,
        compact,
        base=card.get("left_base"),
        has_baseline=has_baseline,
    )
    right = _kpi_side_stack(
        card.get("right"),
        card.get("unit") or "",
        card.get("right_prev"),
        has_previous,
        half,
        compact,
        base=card.get("right_base"),
        has_baseline=has_baseline,
    )
    split = Table([[left, right]], colWidths=[half, half])
    split.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LINEAFTER", (0, 0), (0, 0), 0.5, colors.HexColor("#e5e7eb")),
            ]
        )
    )
    rows: list = [[header], [split]]
    asym = _asymmetry_pct(card.get("left"), card.get("right"))
    if asym is not None:
        pct_w = 16
        bar_w = max(inner_w - pct_w, 18)
        bar_row = Table(
            [[_AsymBar(asym, bar_w, height=3), Paragraph(f"{asym:.0f}%", pct_style)]],
            colWidths=[bar_w, pct_w],
        )
        bar_row.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        rows.append([bar_row])
    body = Table(rows, colWidths=[inner_w])
    body.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    return body


def _kpi_single_card(
    card: dict[str, Any],
    width: float,
    has_previous: bool,
    compact: bool,
    *,
    has_baseline: bool = False,
    previous_label: str = "Previous",
) -> Table:
    inner_w = max(width, 20)
    label_style = ParagraphStyle(
        f"KpiMixLabel{int(width)}",
        fontName="Helvetica",
        fontSize=6.5 if compact else 7,
        leading=8 if compact else 9,
        textColor=MUTED,
        alignment=1,
    )
    value_style = ParagraphStyle(
        f"KpiMixValue{int(width)}",
        fontName="Helvetica-Bold",
        fontSize=13 if compact else 18,
        leading=15 if compact else 21,
        textColor=NAVY,
        alignment=1,
        spaceBefore=2 if compact else 4,
    )
    unit_style = ParagraphStyle(
        f"KpiMixUnit{int(width)}",
        fontName="Helvetica",
        fontSize=6.5 if compact else 7,
        leading=8 if compact else 9,
        textColor=MUTED,
        alignment=1,
    )
    delta_style = ParagraphStyle(
        f"KpiMixDelta{int(width)}",
        fontName="Helvetica",
        fontSize=7 if compact else 8,
        leading=9 if compact else 10,
        alignment=1,
        spaceBefore=1 if compact else 4,
    )
    bits = [
        Paragraph(escape((card.get("label") or "").upper()), label_style),
        Paragraph(_fmt(card.get("value")), value_style),
    ]
    unit = escape((card.get("unit") or "").strip())
    if unit:
        bits.append(Paragraph(unit, unit_style))
    for markup in _kpi_compare_markups(
        card.get("value"),
        card.get("prev"),
        card.get("base"),
        has_previous=has_previous,
        has_baseline=has_baseline,
        previous_label=previous_label,
        neutral=bool(card.get("neutral")),
    ):
        bits.append(Paragraph(markup, delta_style))
    inner = Table([[b] for b in bits], colWidths=[inner_w])
    inner.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    return inner


def _kpi_mixed_grid(
    cards: list[dict[str, Any]],
    has_previous: bool,
    *,
    page_width: float,
    compact: bool = False,
    units: int = 4,
    has_baseline: bool = False,
    previous_label: str = "Previous",
) -> list:
    """Pack KPI cards into equal-height rows, left-aligned."""
    if not cards:
        return []
    unit_w = page_width / units
    pad = 5 if compact else 8
    inner_w = max(unit_w - 2 * pad, 20)
    shells: list[_KpiShell] = []
    for card in cards:
        if card.get("kind") == "bilateral":
            inner = _bilateral_kpi_card(
                card,
                inner_w,
                has_previous,
                compact,
                has_baseline=has_baseline,
            )
        else:
            inner = _kpi_single_card(
                card,
                inner_w,
                has_previous,
                compact,
                has_baseline=has_baseline,
                previous_label=previous_label,
            )
        shells.append(_KpiShell(inner, unit_w, pad=pad))

    for shell in shells:
        shell.wrap(unit_w, 40 * inch)
    shared_h = max(shell.height for shell in shells)
    for shell in shells:
        shell.match_height(shared_h)

    rows: list[list[_KpiShell]] = []
    current: list[_KpiShell] = []
    for shell in shells:
        if len(current) >= units:
            rows.append(current)
            current = [shell]
        else:
            current.append(shell)
    if current:
        rows.append(current)

    tables: list = []
    for packed in rows:
        cells: list = list(packed)
        widths = [unit_w] * len(packed)
        leftover = units - len(packed)
        if leftover:
            cells.append("")
            widths.append(leftover * unit_w)
        row = Table([cells], colWidths=widths)
        row.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ]
            )
        )
        tables.append(row)
    return tables


def _kpi_card_grid(
    cards: list[dict[str, Any]],
    has_previous: bool,
    *,
    per_row: int = 4,
    page_width: float = 7.1 * inch,
    compact: bool = False,
    boxed: bool = True,
    has_baseline: bool = False,
    previous_label: str = "Previous",
) -> list:
    """Metric cards: uppercase label, large centered value, unit, Δ vs previous."""
    if not cards:
        return []
    suffix = "Compact" if compact else ""
    label_style = ParagraphStyle(
        f"KpiLabel{suffix}",
        fontName="Helvetica",
        fontSize=6 if compact else 7,
        leading=8 if compact else 9,
        textColor=MUTED,
        alignment=1,
    )
    value_style = ParagraphStyle(
        f"KpiValue{suffix}",
        fontName="Helvetica-Bold",
        fontSize=12 if compact else 18,
        leading=14 if compact else 21,
        textColor=NAVY,
        alignment=1,
        spaceBefore=1 if compact else 4,
    )
    unit_style = ParagraphStyle(
        f"KpiUnit{suffix}",
        fontName="Helvetica",
        fontSize=6 if compact else 7,
        leading=8 if compact else 9,
        textColor=MUTED,
        alignment=1,
        spaceBefore=0 if compact else 1,
    )
    delta_style = ParagraphStyle(
        f"KpiDelta{suffix}",
        fontName="Helvetica",
        fontSize=7 if compact else 8,
        leading=9 if compact else 10,
        alignment=1,
        spaceBefore=1 if compact else 4,
    )
    col_w = page_width / per_row
    pad = 4 if compact else 10
    inset = 8 if compact else 12
    cells: list = []
    for card in cards:
        label = escape((card.get("label") or "").upper())
        unit = escape((card.get("unit") or "").strip())
        value = card.get("value")
        bits = [
            Paragraph(label, label_style),
            Paragraph(_fmt(value), value_style),
        ]
        if unit:
            bits.append(Paragraph(unit, unit_style))
        foot = (card.get("foot") or "").strip()
        if foot:
            bits.append(Paragraph(escape(foot), unit_style))
        for markup in _kpi_compare_markups(
            value,
            card.get("prev"),
            card.get("base"),
            has_previous=has_previous,
            has_baseline=has_baseline,
            previous_label=previous_label,
            neutral=bool(card.get("neutral")),
        ):
            bits.append(Paragraph(markup, delta_style))
        inner = Table([[b] for b in bits], colWidths=[max(col_w - inset, 20)])
        inner.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        cells.append(inner)

    rows: list = []
    for i in range(0, len(cells), per_row):
        chunk = list(cells[i : i + per_row])
        chunk += [""] * (per_row - len(chunk))
        rows.append(chunk)
    table = Table(rows, colWidths=[col_w] * per_row)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e6edf2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 if compact else 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 if compact else 8),
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
    ]
    if boxed:
        cmds.insert(
            1, ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d7de"))
        )
    table.setStyle(TableStyle(cmds))
    if compact:
        return [table]
    return [table, Spacer(1, 8)]


def _kpi_group_box(
    title: str,
    cards: list[dict[str, Any]],
    has_previous: bool,
    styles,
    *,
    compact: bool = False,
    has_baseline: bool = False,
    previous_label: str = "Previous",
) -> list:
    """Section frame with title inside — matches the Best of Day mockup groups."""
    title_style = ParagraphStyle(
        "KpiGroupTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=NAVY,
        alignment=1,
        spaceAfter=2,
    )
    inner_w = PAGE_CONTENT_WIDTH - 16
    grid_bits = _kpi_mixed_grid(
        cards,
        has_previous,
        page_width=inner_w,
        compact=compact,
        has_baseline=has_baseline,
        previous_label=previous_label,
    )
    if not grid_bits:
        return []
    grid = Table([[bit] for bit in grid_bits], colWidths=[inner_w])
    grid.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    inner = Table(
        [[Paragraph(escape(title.upper()), title_style)], [grid]],
        colWidths=[PAGE_CONTENT_WIDTH],
    )
    inner.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d7de")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (0, 0), 8),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
                ("TOPPADDING", (0, 0), (0, 0), 4),
                ("BOTTOMPADDING", (0, 0), (0, 0), 1),
                ("LEFTPADDING", (0, 1), (0, 1), 4),
                ("RIGHTPADDING", (0, 1), (0, 1), 4),
                ("TOPPADDING", (0, 1), (0, 1), 1),
                ("BOTTOMPADDING", (0, 1), (0, 1), 4),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return [inner, Spacer(1, 4)]


def _section_heading(title: str, title_style: ParagraphStyle) -> Paragraph:
    """Left-aligned section title (no logo)."""
    heading = ParagraphStyle(
        f"HeatSectionLeft{title}",
        parent=title_style,
        alignment=0,
        spaceBefore=10,
        spaceAfter=8,
        leftIndent=0,
        firstLineIndent=0,
    )
    return Paragraph(escape(title.upper()), heading)


def _section_chrome(
    title: str,
    title_style: ParagraphStyle,
    extra: Any = None,
    *,
    subtitle: str = "",
    valign: str = "TOP",
    after_space: float = 12,
    title_size: float = 13,
) -> Table:
    """Title + page-1 logo. ``valign`` is TOP or MIDDLE vs the logo."""
    heading = ParagraphStyle(
        f"HeatChromeTitle{int(title_size)}",
        parent=title_style,
        alignment=0,
        spaceBefore=0,
        spaceAfter=0,
        leftIndent=0,
        firstLineIndent=0,
        fontName="Helvetica-Bold",
        fontSize=title_size,
        leading=title_size + 4,
    )
    sub_style = ParagraphStyle(
        "HeatChromeSub",
        parent=title_style,
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=MUTED,
        alignment=0,
        spaceBefore=0,
        spaceAfter=0,
        leftIndent=0,
        firstLineIndent=0,
    )
    left: list = [Paragraph(escape(title.upper()), heading)]
    if subtitle:
        left.append(Spacer(1, 4))
        left.append(Paragraph(escape(subtitle), sub_style))
    if extra is not None:
        left.append(Spacer(1, 12))
        left.append(extra)
    wrap = Table(
        [[_page_logo_row(left, valign=valign)], [Spacer(1, after_space)]],
        colWidths=[PAGE_CONTENT_WIDTH],
    )
    wrap.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return wrap


def _summary_box(title: str, text: str, styles) -> list:
    heading = ParagraphStyle(
        "SummaryBoxTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=NAVY,
        spaceAfter=4,
    )
    body = [
        Paragraph(escape(title), heading),
        *_notes_flowables(text, styles),
    ]
    inner = Table([[body]], colWidths=[7.1 * inch])
    inner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7fa")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#b7c9d6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return [inner, Spacer(1, 8)]


class _NotesCardBox(Flowable):
    """Shaded notes card. Grows with text; min_height keeps empty cards consistent."""

    def __init__(
        self,
        title: str,
        body_flowables: list,
        width: float,
        height: float,
        *,
        icon: bool = False,
    ):
        Flowable.__init__(self)
        self.title = title
        self.body_flowables = body_flowables
        self.box_width = width
        self.min_height = height
        self._forced_height = 0.0
        self.icon = icon

    def wrap(self, availWidth, availHeight):
        pad = 10
        title_h = 16
        body_w = self.box_width - 2 * pad
        body_h = 0
        for f in self.body_flowables:
            _w, fh = f.wrap(body_w, 40 * inch)
            body_h += fh + 2
        self.width = self.box_width
        content_h = max(self.min_height, pad + title_h + 8 + body_h + pad)
        self.height = max(content_h, self._forced_height)
        return self.width, self.height

    def match_height(self, height: float) -> None:
        self._forced_height = height
        self.height = max(self.height, height)

    def draw(self):
        c = self.canv
        w, h = self.box_width, self.height
        pad = 10
        title_h = 16
        c.setFillColor(colors.HexColor("#f3f4f6"))
        c.setStrokeColor(colors.HexColor("#e5e7eb"))
        c.setLineWidth(0.4)
        c.roundRect(0, 0, w, h, 5, fill=1, stroke=1)

        title_y = h - pad - 8
        text_x = pad
        if self.icon:
            r = 5
            cx = pad + r
            cy = title_y + 2
            c.setStrokeColor(NAVY)
            c.setFillColor(NAVY)
            c.setLineWidth(0.9)
            c.circle(cx, cy, r, stroke=1, fill=0)
            c.circle(cx, cy, r * 0.52, stroke=1, fill=0)
            c.circle(cx, cy, 1.15, stroke=0, fill=1)
            text_x = pad + 2 * r + 6
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(text_x, title_y, self.title)

        y = h - pad - title_h - 8
        body_w = w - 2 * pad
        for f in self.body_flowables:
            _w, fh = f.wrap(body_w, 40 * inch)
            f.drawOn(c, pad, y - fh)
            y -= fh + 2


def _notes_card(
    title: str,
    text: str,
    styles,
    *,
    width: float,
    height: float,
    icon: bool = False,
    heading_as_title: bool = False,
) -> _NotesCardBox:
    """Shaded notes card. Empty text still keeps the box + header."""
    empty_style = ParagraphStyle(
        "Page1NotesEmpty",
        parent=styles["Normal"],
        fontSize=8,
        leading=10.5,
        textColor=MUTED,
        fontName="Helvetica-Oblique",
    )
    body = (text or "").strip()
    if body:
        body_bits = _notes_flowables(
            body,
            styles,
            font_size=8,
            leading=10.5,
            heading_as_title=heading_as_title,
        )
        if not body_bits:
            body_bits = [Paragraph(escape(body).replace("\n", "<br/>"), empty_style)]
    else:
        body_bits = [Paragraph("—", empty_style)]
    return _NotesCardBox(
        title,
        body_bits,
        width,
        height,
        icon=icon,
    )


def _page1_notes_block(
    *,
    mechanical: str,
    training_focus: str,
    notes: str,
    styles,
) -> list:
    """Two tall cards on top, shorter Assessment Notes strip below."""
    page_w = 7.1 * inch
    gap = 0.12 * inch
    top_w = (page_w - gap) / 2
    top_h = 2.2 * inch
    bottom_h = 0.95 * inch
    left = _notes_card(
        "MECHANICAL OBSERVATION",
        mechanical,
        styles,
        width=top_w,
        height=top_h,
        icon=True,
    )
    right = _notes_card(
        "TRAINING FOCUS",
        training_focus,
        styles,
        width=top_w,
        height=top_h,
    )
    left.wrap(top_w, 40 * inch)
    right.wrap(top_w, 40 * inch)
    pair_h = max(left.height, right.height)
    left.match_height(pair_h)
    right.match_height(pair_h)
    zero_pad = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]
    top = Table([[left, "", right]], colWidths=[top_w, gap, top_w])
    top.setStyle(TableStyle(zero_pad))
    bottom = _notes_card(
        "ASSESSMENT NOTES",
        notes,
        styles,
        width=page_w,
        height=bottom_h,
    )
    wrap = Table(
        [[top], [Spacer(1, 12)], [bottom]],
        colWidths=[page_w],
    )
    wrap.setStyle(TableStyle(zero_pad))
    return [wrap]


def _meta_cell(label: str, value, value_style: ParagraphStyle, label_style: ParagraphStyle):
    if isinstance(value, Paragraph):
        value_flow = value
    else:
        value_flow = Paragraph(escape(_dash(value)), value_style)
    return [Paragraph(escape(label), label_style), value_flow]


def _page1_header(
    *,
    current: dict[str, Any],
    bundle: dict[str, Any],
    assessment_type: str,
    styles,
    title: str = "SWING MECHANICS",
    subtitle: str = "Movement sequence and mechanical notes",
    after_space: float = 16,
    extra_meta: Optional[list[tuple[str, Any]]] = None,
) -> list:
    kicker = ParagraphStyle(
        "HeatKicker",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=MUTED,
        spaceAfter=2,
    )
    title_style = ParagraphStyle(
        "HeatPage1Title",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=NAVY,
        spaceAfter=2,
    )
    sub_style = ParagraphStyle(
        "HeatPage1Sub",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=MUTED,
    )
    meta_label = ParagraphStyle(
        "HeatMetaLabel",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=6.5,
        leading=8,
        textColor=MUTED,
    )
    meta_value = ParagraphStyle(
        "HeatMetaValue",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#111521"),
    )
    left = [
        Paragraph("RBI HEAT PLAYER DEVELOPMENT", kicker),
        Paragraph(escape(title.upper()), title_style),
        Paragraph(escape(subtitle), sub_style),
    ]
    top = _page_logo_row(left)

    bio = bundle.get("player_bio") or {}
    video_url = (
        bundle.get("video_analysis_url") or current.get("video_analysis_url") or ""
    ).strip()
    if video_url:
        safe_url = escape(video_url)
        video_value = Paragraph(
            f'<link href="{safe_url}" color="blue"><u>Open analysis</u></link>',
            meta_value,
        )
    else:
        video_value = "—"
    hand = (current.get("hittrax_breakdown") or {}).get("handedness")
    if hand == "left":
        hand_label = "Left"
    elif hand == "right":
        hand_label = "Right"
    else:
        hand_label = None

    row1 = [
        _meta_cell("ATHLETE", current.get("player_name"), meta_value, meta_label),
        _meta_cell(
            "DATE",
            _pretty_date(current.get("assessment_date") or current.get("start_ts")),
            meta_value,
            meta_label,
        ),
        _meta_cell("TYPE", assessment_type or None, meta_value, meta_label),
        _meta_cell("TRAINER", current.get("trainer_name"), meta_value, meta_label),
        _meta_cell("VIDEO", video_value, meta_value, meta_label),
    ]
    row2 = [
        _meta_cell("HEIGHT", bio.get("height"), meta_value, meta_label),
        _meta_cell("WEIGHT", bio.get("weight"), meta_value, meta_label),
        _meta_cell("AGE", bio.get("age_display"), meta_value, meta_label),
        _meta_cell("HANDEDNESS", hand_label, meta_value, meta_label),
    ]
    if extra_meta:
        row2.extend(
            _meta_cell(lab, val, meta_value, meta_label) for lab, val in extra_meta
        )
    meta1 = Table([row1], colWidths=[1.42 * inch] * 5)
    meta2 = Table(
        [row2],
        colWidths=[PAGE_CONTENT_WIDTH / len(row2)] * len(row2),
    )
    meta_style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f7fa")),
            ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e6edf2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
    )
    meta1.setStyle(meta_style)
    meta2.setStyle(meta_style)
    bits: list = [top, Spacer(1, 8), meta1, meta2]
    bits.append(Spacer(1, after_space))
    return bits


def _wellness_mini_card(
    label: str,
    value_text: str,
    foot: str,
    value_color,
    width: float,
    styles,
    *,
    foot_color=None,
) -> Table:
    lab = ParagraphStyle(
        f"WqMiniLab{label[:12]}",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=5.5,
        leading=7,
        textColor=MUTED,
        alignment=1,
    )
    val = ParagraphStyle(
        f"WqMiniVal{label[:12]}",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        textColor=value_color,
        alignment=1,
        spaceBefore=2,
    )
    ft = ParagraphStyle(
        f"WqMiniFoot{label[:12]}",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=5.5,
        leading=7,
        textColor=foot_color if foot_color is not None else MUTED,
        alignment=1,
        spaceBefore=1,
    )
    inner = Table(
        [
            [Paragraph(escape(label.upper()), lab)],
            [Paragraph(escape(value_text), val)],
            [Paragraph(escape(foot), ft)],
        ],
        colWidths=[width],
    )
    inner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    return inner


def _wellness_score_foot(value: Optional[float]) -> tuple[str, Any]:
    return (
        report_wellness.score_band_label(value),
        colors.HexColor(report_wellness.score_band_hex(value)),
    )


def _wellness_block_cards(block: dict[str, Any], styles) -> Table:
    page_w = PAGE_CONTENT_WIDTH
    n = 7
    gap = 0.06 * inch
    card_w = (page_w - gap * (n - 1)) / n
    avgs = block.get("metric_avgs") or {}
    overall = block.get("overall_avg")
    overall_foot, overall_foot_c = _wellness_score_foot(overall)
    sleep = avgs.get("sleep_duration")
    sleep_foot, sleep_foot_c = _wellness_score_foot(sleep)
    muscle = avgs.get("general_muscle_soreness")
    muscle_foot, muscle_foot_c = _wellness_score_foot(muscle)
    stress = avgs.get("stress")
    stress_foot, stress_foot_c = _wellness_score_foot(stress)
    diet = avgs.get("diet")
    diet_foot, diet_foot_c = _wellness_score_foot(diet)
    arm = avgs.get("arm_readiness")
    arm_foot, arm_foot_c = _wellness_score_foot(arm)
    specs = [
        (
            "Overall Wellness",
            f"{overall:.1f} / 5" if overall is not None else "—",
            overall_foot,
            overall_foot_c,
            overall_foot_c,
        ),
        (
            "Sleep Duration",
            f"{sleep:.1f} / 5" if sleep is not None else "—",
            sleep_foot,
            sleep_foot_c,
            sleep_foot_c,
        ),
        (
            "Muscle Readiness",
            f"{muscle:.1f} / 5" if muscle is not None else "—",
            muscle_foot,
            muscle_foot_c,
            muscle_foot_c,
        ),
        (
            "Stress Level",
            f"{stress:.1f} / 5" if stress is not None else "—",
            stress_foot,
            stress_foot_c,
            stress_foot_c,
        ),
        (
            "Nutrition",
            f"{diet:.1f} / 5" if diet is not None else "—",
            diet_foot,
            diet_foot_c,
            diet_foot_c,
        ),
        (
            "Arm Readiness",
            f"{arm:.1f} / 5" if arm is not None else "—",
            arm_foot,
            arm_foot_c,
            arm_foot_c,
        ),
    ]
    bw_first, bw_last = block.get("bw_first"), block.get("bw_last")
    bw_change = block.get("bw_change")
    if bw_first is not None and bw_last is not None:
        bw_text = f"{bw_first:.0f} → {bw_last:.0f}"
        ch = f"Change: {bw_change:+.0f} lb" if bw_change is not None else "Change"
    else:
        bw_text = "—"
        ch = "Change"
    specs.append(("Bodyweight", bw_text, ch, NAVY, MUTED))
    cells = [
        _wellness_mini_card(lab, val, foot, col, card_w, styles, foot_color=foot_c)
        for lab, val, foot, col, foot_c in specs
    ]
    row: list = []
    widths: list = []
    for i, cell in enumerate(cells):
        if i:
            row.append("")
            widths.append(gap)
        row.append(cell)
        widths.append(card_w)
    table = Table([row], colWidths=widths)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _wellness_daily_table(block: dict[str, Any], styles) -> Table:
    head = ParagraphStyle(
        "WqTblHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=6,
        leading=8,
        textColor=colors.white,
        alignment=1,
    )
    cell = ParagraphStyle(
        "WqTblCell",
        parent=styles["Normal"],
        fontSize=6.5,
        leading=8,
        textColor=NAVY,
        alignment=1,
    )
    headers = [
        "DATE",
        "FATIGUE",
        "SLEEP",
        "SORENESS",
        "STRESS",
        "DIET",
        "ARM READY",
        "WEIGHT (LB)",
        "WELLNESS SCORE",
    ]
    data = [[Paragraph(h, head) for h in headers]]
    score_fills: list[str] = []
    rows = list(block.get("rows") or [])
    if len(rows) > 12:
        rows = rows[-12:]
    for r in rows:
        day = r.get("date")
        date_s = day.strftime("%b %d") if hasattr(day, "strftime") else "—"
        wt = r.get("weight")
        wt_s = f"{wt:.0f}" if wt is not None else "—"
        well = r.get("wellness")
        data.append(
            [
                Paragraph(escape(date_s), cell),
                Paragraph(escape(str(r.get("fatigue") or "—")), cell),
                Paragraph(escape(str(r.get("sleep") or "—")), cell),
                Paragraph(escape(str(r.get("soreness") or "—")), cell),
                Paragraph(escape(str(r.get("stress") or "—")), cell),
                Paragraph(escape(str(r.get("diet") or "—")), cell),
                Paragraph(escape(str(r.get("arm") or "—")), cell),
                Paragraph(escape(wt_s), cell),
                Paragraph(
                    escape(f"{well:.1f}" if well is not None else "—"), cell
                ),
            ]
        )
        score_fills.append(report_wellness.bucket_bg_hex(well))
    col_w = [
        0.85 * inch,
        0.68 * inch,
        0.62 * inch,
        0.78 * inch,
        0.62 * inch,
        0.55 * inch,
        0.82 * inch,
        0.92 * inch,
        1.26 * inch,
    ]
    table = Table(data, colWidths=col_w, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
    ]
    for i, fill in enumerate(score_fills, start=1):
        cmds.append(("BACKGROUND", (8, i), (8, i), colors.HexColor(fill)))
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (7, i), colors.HexColor("#f8fafc")))
    table.setStyle(TableStyle(cmds))
    return table


def _wellness_page_flow(
    *,
    current: dict[str, Any],
    bundle: dict[str, Any],
    assessment_type: str,
    styles,
    section: ParagraphStyle,
) -> list:
    block = bundle.get("wellness") or {}
    charts = bundle.get("_wellness_chart_images")
    if charts is None:
        charts = report_wellness.build_wellness_chart_images(block)
        bundle["_wellness_chart_images"] = charts
    n = int(block.get("n_checkins") or 0)
    flow = [
        _section_chrome(
            "Wellness & Readiness",
            section,
            subtitle="Self-reported check-ins for this training block.",
            title_size=20,
            after_space=8,
        )
    ]
    h_style = ParagraphStyle(
        "WqSec",
        parent=section,
        fontSize=9,
        leading=11,
        spaceBefore=4,
        spaceAfter=4,
    )
    note = ParagraphStyle(
        "WqNote",
        parent=styles["Normal"],
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor("#1d4ed8"),
        alignment=0,
    )
    mute = ParagraphStyle(
        "WqMute",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        textColor=MUTED,
    )

    flow.append(_wellness_block_cards(block, styles))
    flow.append(Spacer(1, 6))
    banner = Table(
        [
            [
                Paragraph(
                    "All metrics are self-reported on a scale of 1 (Very Poor) to 5 "
                    "(Excellent). Higher scores are better.",
                    note,
                )
            ]
        ],
        colWidths=[PAGE_CONTENT_WIDTH],
    )
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eff6ff")),
                ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#bfdbfe")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    flow.append(banner)
    flow.append(Spacer(1, 8))

    trend_png = charts.get("trend")
    donut_png = charts.get("donut")
    left_bits: list = [Paragraph("OVERALL WELLNESS SCORE TREND", h_style)]
    if trend_png:
        img = Image(BytesIO(trend_png), width=4.55 * inch, height=1.62 * inch)
        img.hAlign = "LEFT"
        left_bits.append(img)
    if n:
        left_bits.append(
            Paragraph(f"{n} check-in{'s' if n != 1 else ''} in this training block.", mute)
        )
    right_bits: list = [Paragraph("WELLNESS DISTRIBUTION", h_style)]
    if donut_png:
        dimg = Image(BytesIO(donut_png), width=1.55 * inch, height=1.55 * inch)
        dimg.hAlign = "CENTER"
        right_bits.append(dimg)
    pair = Table(
        [[left_bits, right_bits]],
        colWidths=[4.7 * inch, 2.4 * inch],
    )
    pair.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    flow.append(pair)
    flow.append(Spacer(1, 8))
    flow.append(Paragraph("RECOVERY PROFILE (TRAINING BLOCK AVERAGES)", h_style))
    rec_png = charts.get("recovery")
    if rec_png:
        rimg = Image(BytesIO(rec_png), width=PAGE_CONTENT_WIDTH, height=1.72 * inch)
        rimg.hAlign = "LEFT"
        flow.append(rimg)
    elif not n:
        flow.append(Paragraph("No wellness check-ins in this training block.", mute))
    flow.append(Spacer(1, 8))
    flow.append(Paragraph("DAILY WELLNESS BREAKDOWN", h_style))
    if n:
        flow.append(_wellness_daily_table(block, styles))
    usable_h = 11 * inch - 0.5 * inch - 0.65 * inch
    return [
        KeepInFrame(
            PAGE_CONTENT_WIDTH,
            usable_h,
            flow,
            mode="shrink",
            hAlign="LEFT",
            vAlign="TOP",
        )
    ]


def _swing_sequence_table(
    mechanics_by_slot: dict[str, list],
    phase_notes_map: dict,
    styles,
) -> Table:
    header_style = ParagraphStyle(
        "SeqHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=colors.white,
        alignment=1,
    )
    caption_style = ParagraphStyle(
        "SeqCaption",
        parent=styles["Normal"],
        fontSize=6.5,
        leading=8,
        textColor=MUTED,
        alignment=1,
        spaceBefore=2,
    )
    badge_style = ParagraphStyle(
        "SeqBadge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=6,
        leading=8,
        alignment=1,
        spaceBefore=0,
        spaceAfter=0,
        textColor=colors.HexColor("#111521"),
    )
    col_w = 7.1 * inch / 5
    wrapped_cols = []
    for slot, label in MECHANICS_SEQUENCE:
        status, caption = _phase_note_entry(phase_notes_map.get(slot))
        items = mechanics_by_slot.get(slot) or []
        first = next((i for i in items if i.get("bytes")), None)
        if not caption and first:
            caption = str(first.get("caption") or "").strip()
        header = Table(
            [[Paragraph(escape(label), header_style)]],
            colWidths=[col_w],
        )
        header.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        body_bits: list = [header]
        body_bits.append(
            _PhasePhotoFrame(
                first["bytes"] if first else None,
                PHASE_PHOTO_WIDTH,
                PHASE_PHOTO_HEIGHT,
            )
        )
        if status in PHASE_STATUS_COLORS:
            bg, fg, text = PHASE_STATUS_COLORS[status]
            badge = Table(
                [[Paragraph(
                    f'<font color="{fg}">{escape(text)}</font>',
                    badge_style,
                )]],
                colWidths=[col_w - 10],
            )
            badge.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), bg),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 2),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]
                )
            )
            body_bits.append(Spacer(1, 4))
            body_bits.append(badge)
        if caption:
            body_bits.append(
                Paragraph(
                    _inline_markdown_to_rl(caption),
                    caption_style,
                )
            )
        wrapped_cols.append(body_bits)
    table = Table([wrapped_cols], colWidths=[col_w] * 5)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#b7c9d6")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _trainer_visual_cell(
    item: dict,
    caption_style: ParagraphStyle,
    *,
    label_override: str | None = None,
    max_width: float = 3.3 * inch,
    max_height: float = 3.0 * inch,
    include_slot_label: bool = True,
):
    raw = item.get("bytes")
    if not raw:
        return None
    img = _fit_image(raw, max_width, max_height)
    if img is None:
        return None
    cap = (item.get("caption") or "").strip()
    slot = (item.get("slot") or "").strip()
    label_bits: list[str] = []
    if include_slot_label:
        if label_override:
            label_bits.append(label_override)
        elif slot and slot not in ("other", "context"):
            label_bits.append(slot.replace("_", " ").title())
    if cap:
        label_bits.append(escape(cap))
    block: list = [img]
    if label_bits:
        block.append(Paragraph(" — ".join(label_bits), caption_style))
    return block


class _HeatCanvas(Canvas):
    """Draw footer after the full pass so we know total page count."""

    def __init__(self, *args, heat_player="", heat_type="", heat_date="", **kwargs):
        Canvas.__init__(self, *args, **kwargs)
        self._heat_player = heat_player
        self._heat_type = heat_type
        self._heat_date = heat_date
        self._saved_states: list[dict] = []

    def showPage(self):
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        states = list(self._saved_states)
        total = len(states)
        for state in states:
            self.__dict__.update(state)
            self._paint_footer(total)
            Canvas.showPage(self)
        Canvas.save(self)

    def _paint_footer(self, total: int) -> None:
        self.saveState()
        self.setStrokeColor(colors.HexColor("#d0d7de"))
        self.setLineWidth(0.4)
        y = 0.48 * inch
        left = 0.7 * inch
        right = letter[0] - 0.7 * inch
        self.line(left, y, right, y)
        self.setFillColor(MUTED)
        self.setFont("Helvetica", 8)
        meta = "  ·  ".join(
            p for p in (self._heat_player, self._heat_type, self._heat_date) if p
        )
        if meta:
            self.drawString(left, 0.32 * inch, meta)
        page_num = getattr(self, "_pageNumber", None) or getattr(self, "page", 1)
        page_label = f"{page_num} of {total}"
        self.drawRightString(right, 0.32 * inch, page_label)
        self.restoreState()


def _heat_canvas_factory(player: str, atype: str, adate: str):
    def _factory(*args, **kwargs):
        return _HeatCanvas(
            *args,
            heat_player=player,
            heat_type=atype,
            heat_date=adate,
            **kwargs,
        )

    return _factory


def _normalize_page_filter(pages: Any) -> Optional[set[str]]:
    """None / 'all' → every page. Otherwise a set of PDF_PAGE_KEYS."""
    if pages is None or pages == "all":
        return None
    if isinstance(pages, str):
        pages = [pages]
    wanted = {str(p).strip() for p in pages if str(p).strip()}
    unknown = wanted - set(PDF_PAGE_KEYS)
    if unknown:
        raise ValueError(
            f"Unknown PDF page key(s): {sorted(unknown)}. "
            f"Use one of: {', '.join(PDF_PAGE_KEYS)}"
        )
    return wanted


def _wants_page(wanted: Optional[set[str]], key: str) -> bool:
    return wanted is None or key in wanted


def _collect_story_pages(
    bundle: dict[str, Any],
    *,
    pages: Any = None,
) -> list[tuple[str, list]]:
    """
    Build named page flowables (no PageBreaks).

    ``pages`` limits which sections are built (and which charts are generated).
    """
    wanted = _normalize_page_filter(pages)
    current = bundle["current"]
    previous = bundle.get("previous")
    baseline = bundle.get("baseline")
    has_previous = previous is not None
    has_baseline = baseline is not None

    styles = getSampleStyleSheet()
    section = ParagraphStyle(
        "HeatSection",
        parent=styles["Heading2"],
        textColor=NAVY,
        fontSize=13,
        spaceBefore=10,
        spaceAfter=8,
        keepWithNext=1,
        alignment=0,
        leftIndent=0,
        firstLineIndent=0,
    )
    subsection = ParagraphStyle(
        "HeatSubsection",
        parent=styles["Heading3"],
        textColor=NAVY,
        fontSize=11,
        spaceBefore=10,
        spaceAfter=6,
        keepWithNext=1,
    )
    cell_style = ParagraphStyle(
        "HeatCell",
        parent=styles["Normal"],
        fontSize=9,
        leading=11,
    )

    assessment_type = _assessment_type_label(current)
    previous_label = _previous_label(current)
    if has_previous and has_baseline:
        vs_peer = "previous and initial"
    else:
        vs_peer = previous_label.lower()
    blast = current.get("blast") or {}
    ht = current.get("hittrax") or {}
    vald = current.get("vald") or {}
    prev_b = (previous or {}).get("blast") or {}
    prev_h = (previous or {}).get("hittrax") or {}
    prev_v = (previous or {}).get("vald") or {}
    base_b = (baseline or {}).get("blast") or {}
    base_h = (baseline or {}).get("hittrax") or {}
    base_v = (baseline or {}).get("vald") or {}
    used_blast = bool(current.get("used_blast", True))
    used_hittrax = bool(current.get("used_hittrax", True))
    used_vald = bool(current.get("used_vald", True))

    trainer_visuals = bundle.get("trainer_visuals") or []
    trainer_visuals = [
        item
        for item in trainer_visuals
        if not (
            ((item.get("slot") or "").strip() == "blast" and not used_blast)
            or ((item.get("slot") or "").strip() == "hittrax" and not used_hittrax)
            or ((item.get("slot") or "").strip() == "vald" and not used_vald)
        )
    ]
    mechanics_slots = {slot for slot, _label in MECHANICS_SEQUENCE}
    mechanics_items = [
        i for i in trainer_visuals if (i.get("slot") or "") in mechanics_slots
    ]
    other_items = [
        i for i in trainer_visuals if (i.get("slot") or "") not in mechanics_slots
    ]
    mechanics_by_slot: dict[str, list] = {slot: [] for slot, _ in MECHANICS_SEQUENCE}
    for item in mechanics_items:
        slot = (item.get("slot") or "").strip()
        if slot in mechanics_by_slot:
            mechanics_by_slot[slot].append(item)
    phase_notes_map = bundle.get("mechanics_phase_notes") or {}
    if isinstance(phase_notes_map, str):
        try:
            phase_notes_map = json.loads(phase_notes_map)
        except (TypeError, ValueError, json.JSONDecodeError):
            phase_notes_map = {}
    if not isinstance(phase_notes_map, dict):
        phase_notes_map = {}

    need_ht_charts = used_hittrax and (
        _wants_page(wanted, "location")
        or _wants_page(wanted, "contact")
        or _wants_page(wanted, "flight_spray")
    )
    chart_images: dict[str, Any] = {}
    if need_ht_charts:
        chart_images = bundle.get("_hittrax_chart_images")
        if chart_images is None:
            contacts = current.get("hittrax_contacts") or []
            chart_images = report_charts.build_hittrax_chart_images(contacts)
            bundle["_hittrax_chart_images"] = chart_images
    chart_context = bundle.get("hittrax_chart_context") or {}

    out: list[tuple[str, list]] = []

    if _wants_page(wanted, "mechanics"):
        flow: list = []
        flow.extend(
            _page1_header(
                current=current,
                bundle=bundle,
                assessment_type=assessment_type,
                styles=styles,
                title="Hitting Assessment",
                subtitle=(
                    "Swing mechanics, batted-ball results, contact location, "
                    "and Best of Day force metrics from this session."
                ),
                after_space=12,
            )
        )
        flow.append(
            _swing_sequence_table(mechanics_by_slot, phase_notes_map, styles)
        )
        flow.append(Spacer(1, 16))
        flow.extend(
            _page1_notes_block(
                mechanical=bundle.get("mechanical_summary") or "",
                training_focus=bundle.get("training_focus") or "",
                notes=bundle.get("notes") or current.get("notes") or "",
                styles=styles,
            )
        )
        out.append(("mechanics", flow))

    if (used_hittrax or used_blast) and _wants_page(wanted, "batted_ball"):
        if used_hittrax:
            q2, q3 = ht.get("la_hard_hit_q2"), ht.get("la_hard_hit_q3")
            sd_hh = ht.get("la_hard_hit_sd")
            la_foot = ""
            if q2 is not None and q3 is not None:
                la_foot = f"Q2–Q3 {_fmt(q2)}–{_fmt(q3)}°"
                if sd_hh is not None:
                    la_foot += f"  ·  SD {_fmt(sd_hh)}°"
            init_h = base_h if has_baseline else prev_h
            has_init = has_baseline or has_previous
            kpi_sub = (
                "Peak, average, and 90th-percentile exit velocity, plus average "
                "launch angle on hard-hit balls."
            )
            if has_init:
                kpi_sub += (
                    " Changes on these cards are versus the initial assessment."
                )
            flow = [
                _section_chrome(
                    "Top Performance Metrics",
                    section,
                    title_size=20,
                    subtitle=kpi_sub,
                )
            ]
            flow.append(Spacer(1, 10))
            flow.extend(
                _kpi_card_grid(
                    [
                        {
                            "label": "Peak EV",
                            "unit": "mph",
                            "value": ht.get("peak_ev"),
                            "prev": init_h.get("peak_ev"),
                        },
                        {
                            "label": "Avg EV",
                            "unit": "mph",
                            "value": ht.get("avg_ev"),
                            "prev": init_h.get("avg_ev"),
                        },
                        {
                            "label": "90th EV",
                            "unit": "mph",
                            "value": ht.get("p90_ev"),
                            "prev": init_h.get("p90_ev"),
                        },
                        {
                            "label": "Avg LA (Hard Hit)",
                            "unit": "°",
                            "value": ht.get("avg_la_hard_hit"),
                            "prev": init_h.get("avg_la_hard_hit"),
                            "foot": la_foot,
                            "neutral": True,
                        },
                    ],
                    has_init,
                    previous_label="Initial",
                )
            )
            flow.append(_section_heading("Batted Ball Profile", section))
            flow.append(
                _hittrax_profile_table(
                    current_ht=ht,
                    previous_ht=prev_h,
                    has_previous=has_previous,
                    cell_style=cell_style,
                    previous_label=previous_label,
                    baseline_ht=base_h,
                    has_baseline=has_baseline,
                )
            )
            if used_blast:
                flow.append(_section_heading("Swing Metrics", section))
                flow.extend(
                    _blast_side_by_side_table(
                        current_date=current.get("assessment_date")
                        or current.get("start_ts"),
                        current_blast=blast,
                        previous_blast=prev_b,
                        baseline_blast=base_b,
                        has_previous=has_previous,
                        has_baseline=has_baseline,
                        section_style=section,
                        cell_style=cell_style,
                        previous_label=previous_label,
                    )
                )
        else:
            flow = [_section_chrome("Swing Metrics", section)]
            flow.extend(
                _blast_side_by_side_table(
                    current_date=current.get("assessment_date")
                    or current.get("start_ts"),
                    current_blast=blast,
                    previous_blast=prev_b,
                    baseline_blast=base_b,
                    has_previous=has_previous,
                    has_baseline=has_baseline,
                    section_style=section,
                    cell_style=cell_style,
                    previous_label=previous_label,
                )
            )
        out.append(("batted_ball", flow))

    if used_hittrax and _wants_page(wanted, "location"):
        loc_table = _hittrax_location_breakdown_table(
            current_date=current.get("assessment_date") or current.get("start_ts"),
            current_breakdown=current.get("hittrax_breakdown") or {},
            previous_breakdown=(previous or {}).get("hittrax_breakdown") or {},
            baseline_breakdown=(baseline or {}).get("hittrax_breakdown") or {},
            has_previous=has_previous,
            has_baseline=has_baseline,
            section_style=section,
            cell_style=cell_style,
            previous_label=previous_label,
        )
        zone_aspect = 4.4 / 4.2
        page_w = PAGE_CONTENT_WIDTH
        gap = 0.12 * inch
        half_w = (page_w - gap) / 2
        img_w = half_w - 0.18 * inch
        ev_img = _ht_image(chart_images, "zone_ev", img_w, zone_aspect)
        la_img = _ht_image(chart_images, "zone_la", img_w, zone_aspect)
        if loc_table or ev_img or la_img:
            flow = [
                _section_chrome(
                    "Batted Ball by Location",
                    section,
                    subtitle=(
                        "Exit velocity, launch angle, and distance by pull / middle / oppo "
                        f"and by pitch zone, versus {vs_peer}. Heatmaps below: warmer cells "
                        "are higher values."
                    ),
                )
            ]
            flow.append(Spacer(1, 10))
            flow.extend(loc_table)
            if ev_img or la_img:
                flow.append(Spacer(1, 10))
            cards_row: list = []
            if ev_img:
                cards_row.append(
                    _chart_card(
                        "Average Exit Velocity by Zone",
                        ev_img,
                        chart_context.get("zone_ev") or "",
                        half_w,
                        styles,
                    )
                )
            if la_img:
                cards_row.append(
                    _chart_card(
                        "Average Launch Angle by Zone",
                        la_img,
                        chart_context.get("zone_la") or "",
                        half_w,
                        styles,
                    )
                )
            if len(cards_row) == 2:
                pair = Table(
                    [[cards_row[0], "", cards_row[1]]],
                    colWidths=[half_w, gap, half_w],
                )
                pair.setStyle(
                    TableStyle(
                        [
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                            ("TOPPADDING", (0, 0), (-1, -1), 0),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                        ]
                    )
                )
                flow.append(pair)
            elif cards_row:
                flow.append(cards_row[0])
            out.append(("location", flow))

    if used_hittrax and _wants_page(wanted, "contact"):
        page_w = PAGE_CONTENT_WIDTH
        gap = 0.12 * inch
        half_w = (page_w - gap) / 2
        inner_w = half_w - 0.18 * inch
        vert_aspect = 4.8 / 4.4
        horiz_aspect = 4.6 / 4.8
        poi_aspect = 4.8 / 4.6
        # ~10% smaller so the two plate views + POI stay on one letter page.
        pair_h = min(inner_w * vert_aspect, inner_w * horiz_aspect) * 0.90
        vert_img = _ht_image(
            chart_images, "plate_vert", pair_h / vert_aspect, vert_aspect
        )
        horiz_img = _ht_image(
            chart_images, "plate_horiz", pair_h / horiz_aspect, horiz_aspect
        )
        poi_w = 3.05 * inch
        poi_img = _ht_image(chart_images, "zone_poi", poi_w, poi_aspect)
        if vert_img or horiz_img or poi_img:
            flow = [
                _section_chrome(
                    "Contact Location",
                    section,
                    subtitle=(
                        "Where the ball is attacked: catcher's-view height and lateral "
                        "location, depth in front of the plate, and average point of "
                        "impact by zone."
                    ),
                    after_space=8,
                )
            ]
            flow.append(Spacer(1, 6))
            cards_row: list = []
            if vert_img:
                cards_row.append(
                    _chart_card(
                        "Contact Location (Vertical)",
                        vert_img,
                        chart_context.get("plate_vert") or "",
                        half_w,
                        styles,
                        caption_gap=8,
                    )
                )
            if horiz_img:
                cards_row.append(
                    _chart_card(
                        "EV by Depth of Contact",
                        horiz_img,
                        chart_context.get("plate_horiz") or "",
                        half_w,
                        styles,
                        caption_gap=8,
                    )
                )
            if len(cards_row) == 2:
                pair = Table(
                    [[cards_row[0], "", cards_row[1]]],
                    colWidths=[half_w, gap, half_w],
                )
                pair.setStyle(
                    TableStyle(
                        [
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                            ("TOPPADDING", (0, 0), (-1, -1), 0),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                        ]
                    )
                )
                flow.append(pair)
            elif cards_row:
                flow.append(cards_row[0])
            if poi_img:
                flow.append(Spacer(1, 6))
                poi_card = _chart_card(
                    "Average Point of Impact (POI) by Zone",
                    poi_img,
                    chart_context.get("zone_poi") or "",
                    3.75 * inch,
                    styles,
                    caption_gap=8,
                )
                centered = Table(
                    [[poi_card]],
                    colWidths=[page_w],
                )
                centered.setStyle(
                    TableStyle(
                        [
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                            ("TOPPADDING", (0, 0), (-1, -1), 0),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                        ]
                    )
                )
                flow.append(centered)
            usable_h = 11 * inch - 0.5 * inch - 0.65 * inch
            out.append(
                (
                    "contact",
                    [
                        KeepInFrame(
                            PAGE_CONTENT_WIDTH,
                            usable_h,
                            flow,
                            mode="shrink",
                            hAlign="LEFT",
                            vAlign="TOP",
                        )
                    ],
                )
            )

    if used_hittrax and _wants_page(wanted, "flight_spray"):
        page_w = PAGE_CONTENT_WIDTH
        spray_aspect = 4.8 / 6.6
        ev_aspect = 4.4 / 5.2
        target_h = 2.9 * inch
        spray_img = _ht_image(
            chart_images, "spray", target_h / spray_aspect, spray_aspect
        )
        ev_img = _ht_image(
            chart_images, "ev_la", target_h / ev_aspect, ev_aspect
        )
        if spray_img or ev_img:
            flow = [
                _section_chrome(
                    "Flight & Spray",
                    section,
                    subtitle=(
                        "Batted-ball flight and field direction. Spray shows where "
                        "balls went and how hard they were hit; EV × LA shows exit "
                        "speed by launch angle, colored by batted-ball type."
                    ),
                )
            ]
            flow.append(Spacer(1, 10))
            cards: list = []
            if spray_img:
                cards.append(
                    _chart_card(
                        "Spray Chart",
                        spray_img,
                        chart_context.get("spray") or "",
                        page_w,
                        styles,
                    )
                )
            if ev_img:
                cards.append(
                    _chart_card(
                        "Exit Velocity × Launch Angle",
                        ev_img,
                        chart_context.get("ev_la") or "",
                        page_w,
                        styles,
                    )
                )
            for i, card in enumerate(cards):
                if i:
                    flow.append(Spacer(1, 8))
                flow.append(card)
            out.append(("flight_spray", flow))

    if used_vald and _wants_page(wanted, "best_of_day"):
        kpi_blocks: list = []
        for spec in VALD_TEST_SECTIONS:
            cards = []
            for item in spec["metrics"]:
                label, key = item[0], item[1]
                unit = item[2] if len(item) > 2 else ""
                if isinstance(key, tuple):
                    left_key, right_key = key
                    cards.append(
                        {
                            "label": label,
                            "unit": unit,
                            "kind": "bilateral",
                            "left": vald.get(left_key),
                            "right": vald.get(right_key),
                            "left_prev": prev_v.get(left_key),
                            "right_prev": prev_v.get(right_key),
                            "left_base": base_v.get(left_key),
                            "right_base": base_v.get(right_key),
                        }
                    )
                else:
                    cards.append(
                        {
                            "label": label,
                            "unit": unit,
                            "kind": "single",
                            "value": vald.get(key),
                            "prev": prev_v.get(key),
                            "base": base_v.get(key),
                        }
                    )
            has_metric = any(
                c.get("value") is not None
                or c.get("prev") is not None
                or c.get("base") is not None
                or c.get("left") is not None
                or c.get("right") is not None
                or c.get("left_prev") is not None
                or c.get("right_prev") is not None
                or c.get("left_base") is not None
                or c.get("right_base") is not None
                for c in cards
            )
            if has_metric:
                kpi_blocks.append((spec["title"], cards))
        bod_summary = bundle.get("best_of_day_summary") or ""

        def _bod_chrome():
            return _section_chrome(
                "Best of Day Metrics",
                section,
                subtitle=(
                    "Session isometric-pull, hop, countermovement-jump, and squat-jump "
                    f"scores versus {vs_peer}. Hop Mean* cards are averages across trials."
                ),
                after_space=6,
            )

        kpi_flow = [_bod_chrome(), Spacer(1, 4)]
        for title, cards in kpi_blocks:
            kpi_flow.extend(
                _kpi_group_box(
                    title,
                    cards,
                    has_previous,
                    styles,
                    compact=True,
                    has_baseline=has_baseline,
                    previous_label=previous_label,
                )
            )
        summary_card = _notes_card(
            "BEST OF DAY METRICS OVERALL SUMMARY",
            bod_summary,
            styles,
            width=PAGE_CONTENT_WIDTH,
            height=0.75 * inch,
            heading_as_title=True,
        )
        usable_h = 11 * inch - 0.5 * inch - 0.65 * inch
        summary_overflows = len(bod_summary.strip()) > 700
        if not summary_overflows:
            kpi_flow.append(summary_card)
        out.append(
            (
                "best_of_day",
                [
                    KeepInFrame(
                        PAGE_CONTENT_WIDTH,
                        usable_h,
                        kpi_flow,
                        mode="shrink",
                        hAlign="LEFT",
                        vAlign="TOP",
                    )
                ],
            )
        )
        if summary_overflows:
            out.append(
                (
                    "best_of_day",
                    [
                        KeepInFrame(
                            PAGE_CONTENT_WIDTH,
                            usable_h,
                            [_bod_chrome(), Spacer(1, 4), summary_card],
                            mode="shrink",
                            hAlign="LEFT",
                            vAlign="TOP",
                        )
                    ],
                )
            )

    caption_style = ParagraphStyle(
        "TrainerCaption",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=MUTED,
        alignment=1,
        spaceAfter=4,
    )
    if other_items and _wants_page(wanted, "other_visuals"):
        flow = [_section_chrome("Other Trainer Visuals", section)]
        cells = []
        for item in other_items:
            cell = _trainer_visual_cell(item, caption_style)
            if cell:
                cells.append(cell)
        _append_image_grid(flow, cells, col_width=3.5 * inch)
        out.append(("other_visuals", flow))

    is_retest = (current.get("assessment_type") or "").strip().lower() == "retest"
    if is_retest and _wants_page(wanted, "wellness"):
        out.append(
            (
                "wellness",
                _wellness_page_flow(
                    current=current,
                    bundle=bundle,
                    assessment_type=assessment_type,
                    styles=styles,
                    section=section,
                ),
            )
        )

    return out


def build_pdf(
    bundle: dict[str, Any],
    output_path: Optional[str] = None,
    *,
    pages: Any = None,
) -> str:
    """
    Write the assessment PDF.

    ``pages``: None/'all' for the full report, or a key / list of keys from
    ``PDF_PAGE_KEYS`` (used by the page-preview notebook).
    """
    current = bundle["current"]
    reports_dir = Path(os.environ.get("REPORT_LOCAL_DIR", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)

    if not output_path:
        player_slug = _slug(current["player_name"])
        aid = current["assessment_id"]
        day = (
            current["start_ts"].strftime("%Y%m%d")
            if isinstance(current["start_ts"], datetime)
            else "date"
        )
        output_path = str(reports_dir / f"{player_slug}_{day}_{aid}.pdf")

    page_list = _collect_story_pages(bundle, pages=pages)
    story: list = []
    for i, (_key, flow) in enumerate(page_list):
        if i:
            story.append(PageBreak())
        story.extend(flow)
    if not story:
        styles = getSampleStyleSheet()
        story.append(
            Paragraph(
                "Nothing to show for this page (tool unchecked, or no data).",
                styles["Normal"],
            )
        )

    assessment_type = _assessment_type_label(current)
    heat_date = _pretty_date(
        current.get("assessment_date") or current.get("start_ts")
    )
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.65 * inch,
    )
    doc.build(
        story,
        canvasmaker=_heat_canvas_factory(
            current.get("player_name") or "",
            assessment_type,
            heat_date,
        ),
    )
    return output_path
