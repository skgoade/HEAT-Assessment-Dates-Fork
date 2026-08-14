"""Generate HEAT assessment draft PDFs with ReportLab."""
from __future__ import annotations

import os
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape

import report_charts
from report_metrics import (
    BLAST_BAT_LABELS,
    BLAST_BAT_ORDER,
    HITTRAX_FIELD_ORDER,
    HITTRAX_ZONE_ORDER,
    VALD_METRIC_UNITS,
    blast_group_stats,
    hittrax_breakdown_value,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

GREEN = colors.HexColor("#1a7f37")
RED = colors.HexColor("#cf222e")
MUTED = colors.HexColor("#57606a")
DELTA_POS_BG = colors.HexColor("#c6efce")
DELTA_NEG_BG = colors.HexColor("#ffc7ce")
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "rbi_heat_logo.png"

# VALD PDF sections: each test type owns its metrics + Looker-style cards.
VALD_TEST_SECTIONS: list[dict[str, Any]] = [
    {
        "title": "Countermovement Jump",
        "metrics": [
            ("CMJ Jump Height (FT)", "cmj_jump_height_ft"),
            ("CMJ Peak Power / BM", "cmj_peak_power_bm"),
            ("CMJ RSI-modified", "cmj_rsi_mod"),
        ],
        "cards": ("cmj_jh_trend", "cmj_rsi_trend"),
    },
    {
        "title": "Squat Jump",
        "metrics": [
            ("SJ Jump Height (FT)", "sj_jump_height_ft"),
            ("SJ Peak Power / BM", "sj_peak_power_bm"),
        ],
        "cards": ("sj_jh_trend", "sj_rfd_bilat"),
    },
    {
        "title": "Hop Test",
        "metrics": [
            ("HJ Best RSI", "hj_best_rsi"),
            ("HJ Best Jump Height", "hj_best_jump_height"),
        ],
        "cards": ("hj_rsi_trend", "hj_force_bilat"),
    },
    {
        "title": "Isometric Mid-Thigh Pull",
        "metrics": [
            ("IMTP Peak Force / BM", "imtp_peak_force_bm"),
            ("IMTP RFD 100ms", "imtp_rfd_100"),
        ],
        "cards": ("imtp_force_trend", "imtp_rfd150_bilat"),
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


def _notes_flowables(notes_text: str, styles) -> list:
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
    body = ParagraphStyle(
        "CoachNotes",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=MUTED,
        spaceBefore=2,
        spaceAfter=4,
    )
    bullet = ParagraphStyle(
        "CoachNotesBullet",
        parent=body,
        leftIndent=14,
        firstLineIndent=-10,
        spaceBefore=1,
        spaceAfter=1,
    )
    heading = ParagraphStyle(
        "CoachNotesHeading",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#0b3d5c"),
        spaceBefore=8,
        spaceAfter=4,
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
            sub.append(Paragraph("Previous vs.<br/>Current", sub_header_style))
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
    return [
        Paragraph("Swing Metrics", section_style),
        table,
    ]


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
            sub.append(Paragraph("Previous vs.<br/>Current", sub_header_style))
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
    return [
        Paragraph("Batted Ball by Location", section_style),
        table,
    ]


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
        headers.append("Δ Prev")
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


def _hittrax_chart_cell(
    chart_images: dict[str, Any],
    chart_context: dict[str, str],
    caption_style: ParagraphStyle,
    key: str,
    width: float,
    aspect: float,
):
    """Image + optional caption for one HitTrax chart, or None if missing."""
    png = chart_images.get(key)
    if not png:
        return None
    img = Image(BytesIO(png), width=width, height=width * aspect)
    img.hAlign = "CENTER"
    ctx = (chart_context.get(key) or "").strip()
    if ctx:
        return [img, Paragraph(escape(ctx), caption_style)]
    return img


def build_pdf(bundle: dict[str, Any], output_path: Optional[str] = None) -> str:
    current = bundle["current"]
    previous = bundle.get("previous")
    baseline = bundle.get("baseline")
    has_previous = previous is not None
    has_baseline = baseline is not None

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

    styles = getSampleStyleSheet()
    subtitle = ParagraphStyle(
        "HeatSub",
        parent=styles["Normal"],
        textColor=colors.HexColor("#444444"),
        fontSize=10,
        spaceAfter=4,
    )
    section = ParagraphStyle(
        "HeatSection",
        parent=styles["Heading2"],
        textColor=colors.HexColor("#0b3d5c"),
        fontSize=13,
        spaceBefore=14,
        spaceAfter=8,
        # Never leave a section heading stranded at the bottom of a page.
        keepWithNext=1,
    )
    cell_style = ParagraphStyle(
        "HeatCell",
        parent=styles["Normal"],
        fontSize=9,
        leading=11,
    )
    brand_style = ParagraphStyle(
        "HeatBrand",
        parent=styles["Normal"],
        textColor=colors.HexColor("#111521"),
        fontSize=11,
        fontName="Helvetica-Bold",
        alignment=1,  # center under logo
        spaceBefore=6,
    )
    player_style = ParagraphStyle(
        "HeatPlayer",
        parent=styles["Heading1"],
        textColor=colors.HexColor("#111521"),
        fontSize=16,
        spaceAfter=4,
    )

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.6 * inch,
    )
    story: list = []

    # --- Header: player meta left, logo + title top-right ---
    assessment_type = _assessment_type_label(current)
    left_bits = [
        Paragraph(f"<b>{current['player_name']}</b>", player_style),
        Paragraph(
            f"{assessment_type} · Assessment ID: {current['assessment_id']}",
            subtitle,
        ),
    ]
    video_url = (
        bundle.get("video_analysis_url")
        or current.get("video_analysis_url")
        or ""
    ).strip()
    if video_url:
        safe_url = escape(video_url)
        left_bits.append(
            Paragraph(
                f'Video Link — <link href="{safe_url}" color="blue"><u>{safe_url}</u></link>',
                subtitle,
            )
        )
    if previous:
        left_bits.append(
            Paragraph(
                f"Previous: #{previous['assessment_id']} ({_date_only(previous['start_ts'])})",
                subtitle,
            )
        )
    if baseline:
        left_bits.append(
            Paragraph(
                f"Baseline: #{baseline['assessment_id']} ({_date_only(baseline['start_ts'])})",
                subtitle,
            )
        )
    left = left_bits

    right_bits: list = []
    if LOGO_PATH.is_file():
        # Native logo art is ~1546×918 — keep that aspect; keep modest so title centers under text
        logo_w = 1.45 * inch
        logo_h = logo_w * (918 / 1546)
        right_bits.append(Image(str(LOGO_PATH), width=logo_w, height=logo_h))
    right_bits.append(Paragraph("Hitting Assessment", brand_style))
    right = Table([[b] for b in right_bits], colWidths=[1.55 * inch])
    right.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (0, 0), 4),
                ("BOTTOMPADDING", (0, 1), (0, -1), 1),
            ]
        )
    )

    header = Table([[left, right]], colWidths=[5.0 * inch, 1.8 * inch])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 8))

    blast = current["blast"]
    ht = current["hittrax"]
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

    # --- Current snapshot (requested metrics) ---
    snap = []
    if used_hittrax:
        snap.append(["Peak Exit Velocity (mph)", _fmt(ht.get("peak_ev"))])
    # --- Blast (overall snapshot prefers Game Bat when present) ---
    if used_blast:
        blast_by_bat = blast.get("by_bat") or []
        game_bat_stats = next(
            (g for g in blast_by_bat if g.get("bat_key") == "game_bat"),
            None,
        )
        snap_blast = game_bat_stats or blast
        snap.extend(
            [
                ["Peak Bat Speed (mph)", _fmt(snap_blast.get("peak_bat_speed"))],
                ["SD Attack Angle (deg)", _fmt(snap_blast.get("sd_attack_angle"))],
            ]
        )
        if game_bat_stats and len(blast_by_bat) > 1:
            # Clarify snapshot is game-bat when multiple bats were used
            snap[-2][0] = "Peak Bat Speed (mph, Game Bat)"
            snap[-1][0] = "SD Attack Angle (deg, Game Bat)"
    if snap:
        story.append(Paragraph("Current Snapshot", section))
        t = Table(snap, colWidths=[3.2 * inch, 2.2 * inch])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f7fa")),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(t)

    contacts = current.get("hittrax_contacts") or []
    chart_images = (
        report_charts.build_hittrax_chart_images(contacts)
        if used_hittrax
        else {}
    )
    chart_context = bundle.get("hittrax_chart_context") or {}
    hittrax_caption_style = ParagraphStyle(
        "HittraxChartCaption",
        parent=styles["Normal"],
        fontSize=7.5,
        leading=9.5,
        textColor=MUTED,
        alignment=1,
        spaceBefore=3,
        spaceAfter=4,
    )

    def _ht_cell(key: str, width: float, aspect: float):
        return _hittrax_chart_cell(
            chart_images, chart_context, hittrax_caption_style, key, width, aspect
        )

    # --- HitTrax profile table + matching flight/spray charts ---
    if used_hittrax:
        story.extend(
        _comparison_table(
            "Batted Ball Profile",
            colors.HexColor("#0b3d5c"),
            colors.white,
            [
                ("Peak EV (mph)", ht.get("peak_ev"), prev_h.get("peak_ev"), base_h.get("peak_ev")),
                ("Avg EV (mph)", ht.get("avg_ev"), prev_h.get("avg_ev"), base_h.get("avg_ev")),
                ("90th EV (mph)", ht.get("p90_ev"), prev_h.get("p90_ev"), base_h.get("p90_ev")),
                (
                    "Avg Launch Angle (°)",
                    ht.get("avg_launch_angle"),
                    prev_h.get("avg_launch_angle"),
                    base_h.get("avg_launch_angle"),
                ),
                (
                    "Avg LA (Hard Hit) (°)",
                    ht.get("avg_la_hard_hit"),
                    prev_h.get("avg_la_hard_hit"),
                    base_h.get("avg_la_hard_hit"),
                ),
                (
                    "Avg EV (LA 5–15°) (mph)",
                    ht.get("avg_ev_ideal_la"),
                    prev_h.get("avg_ev_ideal_la"),
                    base_h.get("avg_ev_ideal_la"),
                ),
                (
                    "Avg Distance (ft)",
                    ht.get("avg_distance"),
                    prev_h.get("avg_distance"),
                    base_h.get("avg_distance"),
                ),
                (
                    "Contacts (n)",
                    ht.get("swing_count"),
                    prev_h.get("swing_count"),
                    base_h.get("swing_count"),
                ),
            ],
            has_previous,
            has_baseline,
            section,
            cell_style,
        )
        )

    # --- Blast: all bat groups side-by-side (page 1 tables stay clean) ---
    if used_blast:
        story.extend(
            _blast_side_by_side_table(
                current_date=current.get("assessment_date") or current.get("start_ts"),
                current_blast=blast,
                previous_blast=prev_b,
                baseline_blast=base_b,
                has_previous=has_previous,
                has_baseline=has_baseline,
                section_style=section,
                cell_style=cell_style,
            )
        )

    # --- HitTrax charts on their own pages (more space; spray emphasized) ---
    if used_hittrax:
        # Stack spray + EV×LA so each can use more of the page than a 2-across row.
        profile_cells = [
            cell
            for cell in (
                _ht_cell("spray", 4.9 * inch, 4.8 / 6.6),
                _ht_cell("ev_la", 4.9 * inch, 4.4 / 5.2),
            )
            if cell is not None
        ]
        if profile_cells:
            story.append(PageBreak())
            story.append(Paragraph("Flight & Spray", section))
            story.append(Spacer(1, 4))
            _append_image_grid(
                story,
                profile_cells,
                col_width=7.1 * inch,
                per_row=1,
                pad=4,
                bottom_pad=6,
                after_space=4,
            )

        story.append(PageBreak())
        story.extend(
            _hittrax_location_breakdown_table(
                current_date=current.get("assessment_date") or current.get("start_ts"),
                current_breakdown=current.get("hittrax_breakdown") or {},
                previous_breakdown=(previous or {}).get("hittrax_breakdown") or {},
                baseline_breakdown=(baseline or {}).get("hittrax_breakdown") or {},
                has_previous=has_previous,
                has_baseline=has_baseline,
                section_style=section,
                cell_style=cell_style,
            )
        )
        zone_cells = [
            cell
            for cell in (
                _ht_cell("zone_ev", 3.35 * inch, 1.0),
                _ht_cell("zone_la", 3.35 * inch, 1.0),
            )
            if cell is not None
        ]
        if zone_cells:
            story.append(Spacer(1, 10))
            _append_image_grid(story, zone_cells, col_width=3.55 * inch)

        contact_cells = [
            cell
            for cell in (
                _ht_cell("plate_vert", 3.05 * inch, 4.8 / 4.4),
                _ht_cell("plate_horiz", 3.05 * inch, 4.6 / 4.8),
            )
            if cell is not None
        ]
        poi_cell = _ht_cell("zone_poi", 3.55 * inch, 4.8 / 4.6)
        if contact_cells or poi_cell:
            story.append(PageBreak())
            story.append(Paragraph("Contact Location", section))
            story.append(Spacer(1, 4))
            if contact_cells:
                _append_image_grid(
                    story,
                    contact_cells,
                    col_width=3.55 * inch,
                    pad=4,
                    bottom_pad=4,
                    after_space=2,
                )
            if poi_cell:
                _append_image_grid(
                    story,
                    [poi_cell],
                    col_width=7.1 * inch,
                    per_row=1,
                    pad=4,
                    bottom_pad=4,
                    after_space=0,
                )

    # --- VALD ForceDecks: one section per test type (metrics + visuals) ---
    vald_series = current.get("vald_series") or {}
    vald_cards = (
        report_charts.build_vald_chart_images(vald_series) if used_vald else {}
    )
    if used_vald:
        vald_defs = bundle.get("vald_definitions") or []
        vald_def_by_label = {
            (item.get("report_label") or item.get("label") or "").strip(): (
                item.get("description") or ""
            ).strip()
            for item in vald_defs
            if (item.get("report_label") or item.get("label"))
            and (item.get("description") or "").strip()
        }
        vald_unit_by_label = dict(VALD_METRIC_UNITS)
        for item in vald_defs:
            key = (item.get("report_label") or item.get("label") or "").strip()
            unit = (item.get("unit") or "").strip()
            if key and unit:
                vald_unit_by_label[key] = unit
        card_context = bundle.get("vald_card_context") or {}
        vald_caption_style = ParagraphStyle(
            "ValdCardCaption",
            parent=styles["Normal"],
            fontSize=7.5,
            leading=9,
            textColor=MUTED,
            alignment=1,
            spaceBefore=2,
            spaceAfter=4,
        )
        vald_header_bg = colors.HexColor("#111521")
        vald_header_fg = colors.HexColor("#fcba39")

        def _vald_card_cells(keys: tuple[str, ...]) -> list:
            cells: list = []
            for key in keys:
                png = vald_cards.get(key)
                if not png:
                    continue
                img = Image(BytesIO(png), width=3.15 * inch, height=1.64 * inch)
                img.hAlign = "CENTER"
                ctx = (card_context.get(key) or "").strip()
                if ctx:
                    cells.append([img, Paragraph(escape(ctx), vald_caption_style)])
                else:
                    cells.append(img)
            return cells

        vald_blocks: list = []
        for spec in VALD_TEST_SECTIONS:
            metric_rows = [
                (
                    label,
                    vald.get(key),
                    prev_v.get(key),
                    base_v.get(key),
                )
                for label, key in spec["metrics"]
            ]
            cards = _vald_card_cells(spec["cards"])
            has_metric = any(
                curr is not None or prev is not None or base is not None
                for _, curr, prev, base in metric_rows
            )
            if not has_metric and not cards:
                continue
            block: list = []
            block.extend(
                _comparison_table(
                    spec["title"],
                    vald_header_bg,
                    vald_header_fg,
                    metric_rows,
                    has_previous,
                    has_baseline,
                    section,
                    cell_style,
                    definitions=vald_def_by_label or None,
                    units=vald_unit_by_label or None,
                )
            )
            if cards:
                grid = _distributed_image_row(cards)
                if grid is not None:
                    block.append(Spacer(1, 6))
                    block.append(grid)
            vald_blocks.append(KeepTogether(block))

        if vald_blocks:
            if snap or used_hittrax or used_blast or chart_images:
                story.append(PageBreak())
            story.append(Paragraph("VALD ForceDecks", section))
            story.extend(vald_blocks)

    # --- Trainer visuals (uploaded context images) + mechanics phase cards ---
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
    mechanics_labels = {
        "load_phase": "1. Load Phase",
        "load_position": "2. Load Position",
        "stride_phase": "3. Stride Phase",
        "launch_position": "4. Launch Position",
        "impact": "5. Impact",
    }
    phase_notes_map = bundle.get("mechanics_phase_notes") or {}
    if not isinstance(phase_notes_map, dict):
        phase_notes_map = {}

    mechanics_items = [
        i for i in trainer_visuals if (i.get("slot") or "") in mechanics_labels
    ]
    other_items = [
        i for i in trainer_visuals if (i.get("slot") or "") not in mechanics_labels
    ]
    mechanics_by_slot: dict[str, list] = {slot: [] for slot in mechanics_labels}
    for item in mechanics_items:
        slot = (item.get("slot") or "").strip()
        if slot in mechanics_by_slot:
            mechanics_by_slot[slot].append(item)

    phases_with_content = [
        slot
        for slot in mechanics_labels
        if mechanics_by_slot.get(slot)
        or str(phase_notes_map.get(slot) or "").strip()
    ]

    if phases_with_content or other_items:
        story.append(PageBreak())
        caption_style = ParagraphStyle(
            "TrainerCaption",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            textColor=MUTED,
            alignment=1,
            spaceAfter=4,
        )
        phase_header_style = ParagraphStyle(
            "MechanicsPhaseHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=colors.HexColor("#0b3d5c"),
            alignment=0,
            spaceBefore=0,
            spaceAfter=0,
        )
        phase_notes_label_style = ParagraphStyle(
            "MechanicsPhaseNotesLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#0b3d5c"),
            spaceBefore=0,
            spaceAfter=2,
        )

        def _visual_cell(
            item: dict,
            label_override: str | None = None,
            max_width: float = 3.3 * inch,
            max_height: float = 3.0 * inch,
            include_slot_label: bool = True,
        ):
            raw = item.get("bytes")
            if not raw:
                return None
            try:
                image_width, image_height = ImageReader(BytesIO(raw)).getSize()
                scale = min(
                    max_width / image_width,
                    max_height / image_height,
                    1.0,
                )
                img = Image(
                    BytesIO(raw),
                    width=image_width * scale,
                    height=image_height * scale,
                )
                img.hAlign = "CENTER"
            except Exception:
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
            # A table cell already keeps its contents together. Wrapping the
            # cell in KeepTogether reports an effectively infinite height to
            # ReportLab and causes a LayoutError.
            return block

        if phases_with_content:
            story.append(Paragraph("Mechanics", section))
            card_box = colors.HexColor("#b7c9d6")
            card_header_bg = colors.HexColor("#e8f1f8")
            card_w = 3.50 * inch
            photo_max_w = card_w - 0.18 * inch
            photo_max_h = 2.15 * inch
            phase_cards: list = []

            for slot in phases_with_content:
                slot_items = mechanics_by_slot.get(slot) or []
                note_text = str(phase_notes_map.get(slot) or "").strip()
                n_photos = len(slot_items)
                per_row = 2 if n_photos > 1 else 1
                max_w = (photo_max_w / per_row) - 0.06 * inch if per_row == 2 else photo_max_w
                max_h = photo_max_h

                photo_cells = []
                for item in slot_items:
                    cell = _visual_cell(
                        item,
                        include_slot_label=False,
                        max_width=max_w,
                        max_height=max_h,
                    )
                    if cell:
                        photo_cells.append(cell)

                card_rows: list = [
                    [Paragraph(mechanics_labels[slot], phase_header_style)]
                ]
                if photo_cells:
                    for i in range(0, len(photo_cells), per_row):
                        chunk = list(photo_cells[i : i + per_row])
                        while len(chunk) < per_row:
                            chunk.append("")
                        card_rows.append(chunk)
                elif note_text:
                    card_rows.append(
                        [Paragraph("<i>No photo for this phase</i>", caption_style)]
                    )

                if note_text:
                    notes_block = [
                        Paragraph("Phase notes", phase_notes_label_style),
                        *_notes_flowables(note_text, styles),
                    ]
                    card_rows.append([notes_block])

                if len(card_rows) < 2:
                    continue

                use_two = any(isinstance(r, list) and len(r) == 2 for r in card_rows)
                if use_two:
                    built = []
                    span_cmds = []
                    for r_i, row in enumerate(card_rows):
                        if isinstance(row, list) and len(row) == 2:
                            built.append(row)
                        else:
                            cell = row[0] if isinstance(row, list) else row
                            built.append([cell, ""])
                            span_cmds.append(("SPAN", (0, r_i), (1, r_i)))
                    half = card_w / 2
                    card = Table(built, colWidths=[half, half])
                    style_cmds = [
                        ("BACKGROUND", (0, 0), (-1, 0), card_header_bg),
                        ("BOX", (0, 0), (-1, -1), 0.6, card_box),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.5, card_box),
                        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
                        ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                        ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                        ("ALIGN", (0, 1), (-1, -1), "CENTER"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, 0), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                        ("TOPPADDING", (0, 1), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                        *span_cmds,
                    ]
                else:
                    built = [
                        [row[0] if isinstance(row, list) else row] for row in card_rows
                    ]
                    card = Table(built, colWidths=[card_w])
                    style_cmds = [
                        ("BACKGROUND", (0, 0), (-1, 0), card_header_bg),
                        ("BOX", (0, 0), (-1, -1), 0.6, card_box),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.5, card_box),
                        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
                        ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                        ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                        ("ALIGN", (0, 1), (-1, -1), "CENTER"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, 0), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                        ("TOPPADDING", (0, 1), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                    ]
                card.setStyle(TableStyle(style_cmds))
                phase_cards.append(card)

            for i in range(0, len(phase_cards), 2):
                pair = phase_cards[i : i + 2]
                if len(pair) == 1:
                    pair.append("")
                grid = Table([pair], colWidths=[card_w, card_w])
                grid.setStyle(
                    TableStyle(
                        [
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 4),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ]
                    )
                )
                story.append(grid)
                story.append(Spacer(1, 6))

        if other_items:
            if phases_with_content:
                story.append(PageBreak())
            story.append(Paragraph("Other Trainer Visuals", section))
            cells = []
            for item in other_items:
                cell = _visual_cell(item)
                if cell:
                    cells.append(cell)
            _append_image_grid(story, cells, col_width=3.5 * inch)

    # --- Assessment notes (always last) ---
    notes_text = (bundle.get("notes") or current.get("notes") or "").strip()
    if notes_text:
        if (
            snap
            or used_hittrax
            or used_blast
            or used_vald
            or chart_images
            or trainer_visuals
            or phases_with_content
        ):
            story.append(PageBreak())
        story.append(Paragraph("Assessment Notes", section))
        story.extend(_notes_flowables(notes_text, styles))

    doc.build(story)
    return output_path
