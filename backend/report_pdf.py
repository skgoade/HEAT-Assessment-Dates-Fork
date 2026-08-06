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
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "rbi_heat_logo.png"


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


def _comparison_table(
    title: str,
    header_bg: colors.Color,
    header_fg: colors.Color,
    metric_rows: list[tuple[str, Optional[float], Optional[float], Optional[float]]],
    has_previous: bool,
    has_baseline: bool,
    section_style: ParagraphStyle,
    cell_style: ParagraphStyle,
) -> list:
    """Build a section heading + metric table with colored Δ cells."""
    flow: list = [Paragraph(title, section_style)]
    headers = ["Metric", "Current"]
    if has_previous:
        headers.append("Δ Prev")
    if has_baseline:
        headers.append("Δ Base")

    data: list[list[Any]] = [headers]
    for label, curr, prev_v, base_v in metric_rows:
        row: list[Any] = [label, _fmt(curr)]
        if has_previous:
            row.append(_delta_para(curr, prev_v, cell_style))
        if has_baseline:
            row.append(_delta_para(curr, base_v, cell_style))
        data.append(row)

    col_w = [2.4 * inch, 1.3 * inch] + [1.1 * inch] * (len(headers) - 2)
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
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    flow.append(table)
    return flow


def _append_image_grid(
    story: list,
    images: list,
    col_width: float = 3.5 * inch,
    per_row: int = 2,
) -> None:
    """Append ReportLab Images in rows of up to `per_row`."""
    if not images:
        return
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
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)


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
    if used_blast:
        snap.extend(
            [
                ["Peak Bat Speed (mph)", _fmt(blast.get("peak_bat_speed"))],
                ["SD Attack Angle (deg)", _fmt(blast.get("sd_attack_angle"))],
            ]
        )
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

    # --- HitTrax ---
    if used_hittrax:
        story.extend(
        _comparison_table(
            "Batted Ball Profile (HitTrax)",
            colors.HexColor("#0b3d5c"),
            colors.white,
            [
                ("Peak EV", ht.get("peak_ev"), prev_h.get("peak_ev"), base_h.get("peak_ev")),
                ("Avg EV", ht.get("avg_ev"), prev_h.get("avg_ev"), base_h.get("avg_ev")),
                ("90th EV", ht.get("p90_ev"), prev_h.get("p90_ev"), base_h.get("p90_ev")),
                (
                    "Avg Launch Angle",
                    ht.get("avg_launch_angle"),
                    prev_h.get("avg_launch_angle"),
                    base_h.get("avg_launch_angle"),
                ),
                (
                    "Avg LA (Hard Hit)",
                    ht.get("avg_la_hard_hit"),
                    prev_h.get("avg_la_hard_hit"),
                    base_h.get("avg_la_hard_hit"),
                ),
                (
                    "Avg EV (LA 5–15°)",
                    ht.get("avg_ev_ideal_la"),
                    prev_h.get("avg_ev_ideal_la"),
                    base_h.get("avg_ev_ideal_la"),
                ),
                (
                    "Avg Distance",
                    ht.get("avg_distance"),
                    prev_h.get("avg_distance"),
                    base_h.get("avg_distance"),
                ),
                (
                    "Contacts",
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

    # --- Blast ---
    if used_blast:
        story.extend(
        _comparison_table(
            "Blast Motion",
            colors.HexColor("#fcba39"),
            colors.HexColor("#111521"),
            [
                (
                    "Peak Bat Speed",
                    blast.get("peak_bat_speed"),
                    prev_b.get("peak_bat_speed"),
                    base_b.get("peak_bat_speed"),
                ),
                (
                    "Avg Bat Speed",
                    blast.get("avg_bat_speed"),
                    prev_b.get("avg_bat_speed"),
                    base_b.get("avg_bat_speed"),
                ),
                (
                    "SD Bat Speed",
                    blast.get("sd_bat_speed"),
                    prev_b.get("sd_bat_speed"),
                    base_b.get("sd_bat_speed"),
                ),
                (
                    "Avg Attack Angle",
                    blast.get("avg_attack_angle"),
                    prev_b.get("avg_attack_angle"),
                    base_b.get("avg_attack_angle"),
                ),
                (
                    "SD Attack Angle",
                    blast.get("sd_attack_angle"),
                    prev_b.get("sd_attack_angle"),
                    base_b.get("sd_attack_angle"),
                ),
                (
                    "Swings",
                    blast.get("swing_count"),
                    prev_b.get("swing_count"),
                    base_b.get("swing_count"),
                ),
            ],
            has_previous,
            has_baseline,
            section,
            cell_style,
        )
    )

    # --- Batted Ball Visuals (HitTrax charts) ---
    contacts = current.get("hittrax_contacts") or []
    chart_images = (
        report_charts.build_hittrax_chart_images(contacts)
        if used_hittrax
        else {}
    )
    if chart_images:
        story.append(PageBreak())
        story.append(Paragraph("Batted Ball Visuals", section))
        chart_context = bundle.get("hittrax_chart_context") or {}
        caption_style = ParagraphStyle(
            "HittraxChartCaption",
            parent=styles["Normal"],
            fontSize=7.5,
            leading=9,
            textColor=MUTED,
            alignment=1,
            spaceBefore=2,
            spaceAfter=6,
        )
        # Zone + plate charts in a 2-column grid, then EV×LA and spray stacked
        # full-width so the spray chart can be emphasized larger.
        grid_order = (
            ("zone_ev", 3.2 * inch, True),
            ("zone_la", 3.2 * inch, True),
            ("plate_vert", 3.2 * inch, True),
            ("plate_horiz", 3.2 * inch, True),
        )
        chart_cells: list = []
        for key, width, tall in grid_order:
            png = chart_images.get(key)
            if not png:
                continue
            img = Image(
                BytesIO(png),
                width=width,
                height=width * (0.95 if tall else 0.78),
            )
            img.hAlign = "CENTER"
            ctx = (chart_context.get(key) or "").strip()
            if ctx:
                chart_cells.append([img, Paragraph(escape(ctx), caption_style)])
            else:
                chart_cells.append(img)
        _append_image_grid(story, chart_cells)

        # EV×LA + spray on their own page, stacked, kept together
        if chart_images.get("ev_la") or chart_images.get("spray"):
            story.append(PageBreak())
            stacked_blocks: list = []
            stacked = (
                ("ev_la", 4.8 * inch, 0.82),
                ("spray", 6.0 * inch, 0.70),
            )
            for key, width, aspect in stacked:
                png = chart_images.get(key)
                if not png:
                    continue
                img = Image(BytesIO(png), width=width, height=width * aspect)
                img.hAlign = "CENTER"
                stacked_blocks.append(img)
                ctx = (chart_context.get(key) or "").strip()
                if ctx:
                    stacked_blocks.append(Paragraph(escape(ctx), caption_style))
                stacked_blocks.append(Spacer(1, 6))
            if stacked_blocks:
                story.append(KeepTogether(stacked_blocks))

    # --- VALD ForceDecks (best of day) — start on next page with header+table together ---
    if used_vald:
        if snap or used_hittrax or used_blast or chart_images:
            story.append(PageBreak())
        story.append(
        KeepTogether(
            _comparison_table(                "VALD ForceDecks (best of day)",
                colors.HexColor("#111521"),
                colors.HexColor("#fcba39"),
                [
                    (
                        "CMJ Jump Height (FT)",
                        vald.get("cmj_jump_height_ft"),
                        prev_v.get("cmj_jump_height_ft"),
                        base_v.get("cmj_jump_height_ft"),
                    ),
                    (
                        "CMJ Peak Power / BM",
                        vald.get("cmj_peak_power_bm"),
                        prev_v.get("cmj_peak_power_bm"),
                        base_v.get("cmj_peak_power_bm"),
                    ),
                    (
                        "CMJ RSI-modified",
                        vald.get("cmj_rsi_mod"),
                        prev_v.get("cmj_rsi_mod"),
                        base_v.get("cmj_rsi_mod"),
                    ),
                    (
                        "SJ Jump Height (FT)",
                        vald.get("sj_jump_height_ft"),
                        prev_v.get("sj_jump_height_ft"),
                        base_v.get("sj_jump_height_ft"),
                    ),
                    (
                        "SJ Peak Power / BM",
                        vald.get("sj_peak_power_bm"),
                        prev_v.get("sj_peak_power_bm"),
                        base_v.get("sj_peak_power_bm"),
                    ),
                    (
                        "HJ Best RSI",
                        vald.get("hj_best_rsi"),
                        prev_v.get("hj_best_rsi"),
                        base_v.get("hj_best_rsi"),
                    ),
                    (
                        "HJ Best Jump Height",
                        vald.get("hj_best_jump_height"),
                        prev_v.get("hj_best_jump_height"),
                        base_v.get("hj_best_jump_height"),
                    ),
                    (
                        "IMTP Peak Force / BM",
                        vald.get("imtp_peak_force_bm"),
                        prev_v.get("imtp_peak_force_bm"),
                        base_v.get("imtp_peak_force_bm"),
                    ),
                    (
                        "IMTP RFD 100ms",
                        vald.get("imtp_rfd_100"),
                        prev_v.get("imtp_rfd_100"),
                        base_v.get("imtp_rfd_100"),
                    ),
                ],
                has_previous,
                has_baseline,
                section,
                cell_style,
            )
        )
        )

        # Metric definitions (from vald_dictionary when available)
        vald_defs = bundle.get("vald_definitions") or []
        if vald_defs:
            def_heading = ParagraphStyle(
                "ValdDefHeading",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=11,
                textColor=colors.HexColor("#0b3d5c"),
                spaceBefore=10,
                spaceAfter=2,
                keepWithNext=1,
            )
            def_body = ParagraphStyle(
                "ValdDefBody",
                parent=styles["Normal"],
                fontSize=8,
                leading=10,
                textColor=MUTED,
                spaceAfter=6,
            )
            story.append(Paragraph("Metric definitions", section))
            for item in vald_defs:
                label = escape(item.get("label") or "")
                unit = (item.get("unit") or "").strip()
                unit_bit = f" ({escape(unit)})" if unit else ""
                desc = escape(item.get("description") or "")
                story.append(Paragraph(f"{label}{unit_bit}", def_heading))
                story.append(Paragraph(desc, def_body))

    # --- VALD Visuals (Looker-style cards) ---
    vald_series = current.get("vald_series") or {}
    vald_cards = (
        report_charts.build_vald_chart_images(vald_series)
        if used_vald
        else {}
    )
    if vald_cards:
        story.append(PageBreak())
        story.append(Paragraph("VALD ForceDecks", section))
        card_context = bundle.get("vald_card_context") or {}
        caption_style = ParagraphStyle(
            "ValdCardCaption",
            parent=styles["Normal"],
            fontSize=7.5,
            leading=9,
            textColor=MUTED,
            alignment=1,
            spaceBefore=2,
            spaceAfter=6,
        )
        card_order = (
            "imtp_force_trend",
            "imtp_rfd150_bilat",
            "hj_rsi_trend",
            "hj_force_bilat",
            "cmj_jh_trend",
            "cmj_rsi_trend",
            "sj_jh_trend",
            "sj_rfd_bilat",
        )
        card_imgs: list = []
        for key in card_order:
            png = vald_cards.get(key)
            if not png:
                continue
            img = Image(BytesIO(png), width=3.45 * inch, height=1.85 * inch)
            img.hAlign = "CENTER"
            ctx = (card_context.get(key) or "").strip()
            if ctx:
                card_imgs.append(
                    [img, Paragraph(escape(ctx), caption_style)]
                )
            else:
                card_imgs.append(img)
        _append_image_grid(story, card_imgs, col_width=3.55 * inch)

    # --- Trainer visuals (uploaded context images) ---
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
    if trainer_visuals:
        story.append(PageBreak())
        caption_style = ParagraphStyle(
            "TrainerCaption",
            parent=styles["Normal"],
            fontSize=8,
            leading=10,
            textColor=MUTED,
            alignment=1,
            spaceAfter=8,
        )
        mechanics_labels = {
            "load_phase": "1. Load Phase",
            "load_position": "2. Load Position",
            "stride_phase": "3. Stride Phase",
            "launch_position": "4. Launch Position",
            "impact": "5. Impact",
        }
        mechanics_order = {k: i for i, k in enumerate(mechanics_labels)}

        def _visual_cell(
            item: dict,
            label_override: str | None = None,
            max_width: float = 3.3 * inch,
            max_height: float = 3.0 * inch,
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

        mechanics_items = [
            i for i in trainer_visuals if (i.get("slot") or "") in mechanics_labels
        ]
        other_items = [
            i for i in trainer_visuals if (i.get("slot") or "") not in mechanics_labels
        ]
        mechanics_items.sort(
            key=lambda i: mechanics_order.get(i.get("slot") or "", 99)
        )

        if mechanics_items:
            # 3 across keeps all five phases on a single page.
            story.append(Paragraph("Mechanics", section))
            cells = []
            for item in mechanics_items:
                slot = (item.get("slot") or "").strip()
                cell = _visual_cell(
                    item,
                    mechanics_labels.get(slot),
                    max_width=2.1 * inch,
                    max_height=3.3 * inch,
                )
                if cell:
                    cells.append(cell)
            _append_image_grid(story, cells, col_width=2.2 * inch, per_row=3)

        if other_items:
            if mechanics_items:
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
        if snap or used_hittrax or used_blast or used_vald or chart_images or trainer_visuals:
            story.append(PageBreak())
        story.append(Paragraph("Assessment Notes", section))
        story.extend(_notes_flowables(notes_text, styles))

    doc.build(story)
    return output_path
