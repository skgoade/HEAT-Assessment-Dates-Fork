"""Draft trainer notes via Anthropic (Mechanical Observation, Training Focus, Force-Plate Metrics)."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime as dt
from datetime import time, timedelta
from typing import Any, Optional

import requests

import report_metrics

logger = logging.getLogger(__name__)

DRAFT_KINDS = ("mechanical", "training_focus", "best_of_day")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"
REQUEST_TIMEOUT_S = 45

PHASE_LABELS = {
    "load_phase": "Load Phase",
    "load_position": "Load Position",
    "stride_phase": "Stride Phase",
    "launch_position": "Launch Position",
    "impact": "Impact",
}

# Same L/R pairs as Force-Plate Metrics KPI cards.
BILATERAL_PAIRS: list[tuple[str, str, str, str]] = [
    ("IMTP peak force", "imtp_peak_force_l", "imtp_peak_force_r", "N"),
    ("IMTP RFD 150ms", "imtp_rfd_150_l", "imtp_rfd_150_r", "N/s"),
    ("CMJ concentric impulse", "cmj_conc_impulse_l", "cmj_conc_impulse_r", "N·s"),
    ("SJ concentric RFD", "sj_conc_rfd_l", "sj_conc_rfd_r", "N/s"),
    ("SJ landing impulse", "sj_landing_impulse_l", "sj_landing_impulse_r", "N·s"),
]

VALD_SINGLE: list[tuple[str, str, str]] = [
    ("IMTP peak force / BM", "imtp_peak_force_bm", "N/kg"),
    ("IMTP RFD 150ms", "imtp_rfd_150", "N/s"),
    ("Hop best RSI", "hj_best_rsi", "m/s"),
    ("Hop mean RSI", "hj_mean_rsi", "m/s"),
    ("Hop best contact time", "hj_best_contact_time", "ms"),
    ("Hop mean contact time", "hj_mean_contact_time", "ms"),
    ("Hop best jump height", "hj_best_jump_height", "cm"),
    ("Hop mean jump height", "hj_mean_jump_height", "cm"),
    ("Hop best peak force", "hj_best_peak_force", "N"),
    ("Hop mean peak force", "hj_mean_peak_force", "N"),
    ("CMJ relative peak landing force", "cmj_rel_landing_force", "N/cm"),
    ("CMJ jump height", "cmj_jump_height_ft", "cm"),
    ("CMJ RSI-modified", "cmj_rsi_mod", "m/s"),
    ("CMJ concentric duration", "cmj_conc_duration", "ms"),
    ("CMJ concentric impulse", "cmj_conc_impulse", "N·s"),
    ("CMJ eccentric acceleration phase", "cmj_ecc_accel_phase", "s"),
    ("CMJ eccentric braking RFD", "cmj_ecc_braking_rfd", "N/s"),
    ("SJ jump height", "sj_jump_height_ft", "cm"),
    ("SJ relative peak landing force", "sj_rel_landing_force", "N/cm"),
    ("SJ concentric RFD", "sj_conc_rfd", "N/s"),
]

# Slim prior-visit timeline (keeps Anthropic input small).
HEADLINE_KPIS: list[tuple[str, str, str]] = [
    ("IMTP peak force / BM", "imtp_peak_force_bm", "N/kg"),
    ("IMTP RFD 150ms", "imtp_rfd_150", "N/s"),
    ("Hop best RSI", "hj_best_rsi", "m/s"),
    ("Hop best jump height", "hj_best_jump_height", "cm"),
    ("CMJ jump height", "cmj_jump_height_ft", "cm"),
    ("CMJ RSI-modified", "cmj_rsi_mod", "m/s"),
    ("SJ jump height", "sj_jump_height_ft", "cm"),
    ("SJ concentric RFD", "sj_conc_rfd", "N/s"),
]
PRIOR_HISTORY_MAX = 8
# Prior force-plate calendar days by athlete name (weekly tests), not HEAT rows only.
VALD_HISTORY_LOOKBACK_DAYS = 365

SYSTEM_PROMPT = """\
You are a sports performance analyst writing paste-ready force-plate summaries \
for HEAT Baseball coaches (formal athlete development reports).

Voice: direct and to the point. Give the evidence, then what it means. Professional \
but accessible. Not brutal or harsh. Do not pad with fluff.

Never name Blast, HitTrax, or VALD (or ForceDecks). Say "force-plate" or name the \
test (isometric mid-thigh pull, hop test, countermovement jump, squat jump).

Only write about tests present in the input. Never invent a metric, percentile, \
rank, asymmetry, or number that was not provided. This pack does not include league \
percentiles — do not invent "below/above average" from norms; use the measured \
values and provided gaps/deltas only.

Leg roles (use in phrasing; do not explain the rule in the output): right-handed \
hitters — right = drive, left = landing/blocking; left-handed hitters — mirrored. \
Prefer natural phrasing like "right (drive) leg" / "left (landing) leg". If \
handedness is unknown, do not assign drive vs landing legs.

Flag left/right asymmetry of about 10% or greater when gap_pct is provided. State \
which side is higher and whether that fits expected drive vs landing roles or is \
an inversion worth noting. Do not make medical or injury diagnoses — describe \
performance patterns only ("worth monitoring" / "warrants attention" is fine).

If prior session deltas are provided, weave growth or regression into the same \
summary. Output only the draft text for the Overall Summary box: 1–3 narrative \
paragraphs, no headers, no bullet lists, no markdown fences."""


class DraftError(Exception):
    """Trainer-facing draft failure with an HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _pct_gap(left: Any, right: Any) -> Optional[float]:
    try:
        lft = float(left)
        rgt = float(right)
    except (TypeError, ValueError):
        return None
    denom = max(abs(lft), abs(rgt))
    if denom < 1e-9:
        return None
    return round(100.0 * abs(lft - rgt) / denom, 1)


def _phase_entries(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, dict):
        return []
    out: list[dict[str, str]] = []
    for slot, label in PHASE_LABELS.items():
        item = raw.get(slot)
        status = ""
        caption = ""
        if isinstance(item, dict):
            status = str(item.get("status") or "").strip().lower()
            caption = str(
                item.get("caption") or item.get("notes") or item.get("text") or ""
            ).strip()
        elif item:
            caption = str(item).strip()
        if not status and not caption:
            continue
        out.append(
            {
                "phase": label,
                "status": status,
                "caption": caption,
            }
        )
    return out


def _has_phase_text(entries: list[dict[str, str]]) -> bool:
    return any((e.get("status") or e.get("caption")) for e in entries)


def _leg_roles(handedness: Optional[str]) -> Optional[dict[str, str]]:
    if handedness == "right":
        return {
            "hitter": "right-handed",
            "drive": "right",
            "landing": "left",
        }
    if handedness == "left":
        return {
            "hitter": "left-handed",
            "drive": "left",
            "landing": "right",
        }
    return None


def _lookup_handedness(conn, player_name: str, assessment_date: Any) -> Optional[str]:
    start, end = report_metrics._window_bounds({"assessment_date": assessment_date})
    rows = report_metrics._hittrax_rows(conn, player_name, start, end)
    contacts = report_metrics.hittrax_contacts_for_charts(rows)
    if not contacts:
        return None
    votes = [c.get("hand") for c in contacts if c.get("hand") not in (None, "")]
    if not votes:
        return None
    return "left" if report_metrics._modal_hand_is_left(contacts) else "right"


def _vald_pack(vald: dict[str, Any]) -> dict[str, Any]:
    trials = {
        "imtp": int(vald.get("imtp_trials") or 0),
        "hop": int(vald.get("hj_trials") or 0),
        "cmj": int(vald.get("cmj_trials") or 0),
        "sj": int(vald.get("sj_trials") or 0),
    }
    singles = []
    for label, key, unit in VALD_SINGLE:
        val = vald.get(key)
        if val is None:
            continue
        singles.append({"metric": label, "value": val, "unit": unit})
    bilateral = []
    for label, left_key, right_key, unit in BILATERAL_PAIRS:
        left = vald.get(left_key)
        right = vald.get(right_key)
        if left is None and right is None:
            continue
        bilateral.append(
            {
                "metric": label,
                "left": left,
                "right": right,
                "unit": unit,
                "gap_pct": _pct_gap(left, right),
            }
        )
    return {
        "trials": trials,
        "trial_count": sum(trials.values()),
        "metrics": singles,
        "bilateral": bilateral,
    }


def _as_date_str(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value or "")[:10]


def _headline_kpis(vald: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label, key, unit in HEADLINE_KPIS:
        val = vald.get(key)
        if val is None:
            continue
        out.append({"metric": label, "key": key, "value": val, "unit": unit})
    return out


def _headline_map(vald: dict[str, Any]) -> dict[str, Any]:
    return {key: vald.get(key) for _, key, _ in HEADLINE_KPIS if vald.get(key) is not None}


def _slim_session_row(session_date: str, vald: dict[str, Any]) -> dict[str, Any]:
    trials = {
        "imtp": int(vald.get("imtp_trials") or 0),
        "hop": int(vald.get("hj_trials") or 0),
        "cmj": int(vald.get("cmj_trials") or 0),
        "sj": int(vald.get("sj_trials") or 0),
    }
    return {
        "session_date": session_date,
        "trials": trials,
        "trial_count": sum(trials.values()),
        "kpis": _headline_kpis(vald),
    }


def _pct_change(previous: Any, current: Any) -> Optional[float]:
    try:
        prev = float(previous)
        cur = float(current)
    except (TypeError, ValueError):
        return None
    if abs(prev) < 1e-9:
        return None
    return round(100.0 * (cur - prev) / abs(prev), 1)


def _delta_pack(
    *,
    label: str,
    from_date: str,
    to_date: str,
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    for metric_label, key, unit in HEADLINE_KPIS:
        prev = previous.get(key)
        cur = current.get(key)
        if prev is None or cur is None:
            continue
        try:
            delta = round(float(cur) - float(prev), 2)
        except (TypeError, ValueError):
            continue
        changes.append(
            {
                "metric": metric_label,
                "unit": unit,
                "previous": prev,
                "current": cur,
                "delta": delta,
                "pct_change": _pct_change(prev, cur),
            }
        )
    return {
        "label": label,
        "from_date": from_date,
        "to_date": to_date,
        "changes": changes,
    }


def _cap_prior_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep earliest + most recent priors when history is long (max PRIOR_HISTORY_MAX)."""
    if len(rows) <= PRIOR_HISTORY_MAX:
        return rows
    keep_recent = PRIOR_HISTORY_MAX - 1
    earliest = rows[0]
    recent = rows[-keep_recent:]
    if earliest.get("session_date") == recent[0].get("session_date"):
        return recent
    return [earliest] + recent


def _list_prior_vald_session_dates(
    conn, player_name: str, before_date: str
) -> list[str]:
    """
    Distinct force-plate calendar days for this athlete in the lookback window,
    excluding the current assessment date. Sourced from VALD_* tables by name.
    """
    end_day = dt.strptime(before_date, "%Y-%m-%d").date()
    start_day = end_day - timedelta(days=VALD_HISTORY_LOOKBACK_DAYS)
    start_ts = dt.combine(start_day, time.min)
    end_exclusive = dt.combine(end_day, time.min)
    tables = ("VALD_FD_IMTP", "VALD_FD_HJ", "VALD_FD_CMJ", "VALD_FD_SJ")
    unions = " UNION ".join(
        f"SELECT DATE(recordedEST) AS session_date FROM `{t}` "
        f"WHERE athleteName = %s AND recordedEST >= %s AND recordedEST < %s"
        for t in tables
    )
    params: list[Any] = []
    for _ in tables:
        params.extend([player_name, start_ts, end_exclusive])
    sql = f"""
        SELECT session_date FROM ({unions}) AS vald_days
        WHERE session_date IS NOT NULL
        GROUP BY session_date
        ORDER BY session_date ASC
    """
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() or []
    except Exception as e:
        logger.warning("VALD prior session-date lookup failed: %s", e)
        return []
    out: list[str] = []
    for row in rows:
        day = _as_date_str(row.get("session_date"))
        if day and day < before_date:
            out.append(day)
    return out


def _build_vald_history(
    conn, player_name: str, before_date: str, current_vald: dict[str, Any]
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """
    Compact prior force-plate calendar days (VALD by player name) +
    first→current and last-prior→current deltas.
    """
    history: list[dict[str, Any]] = []
    raw_by_date: dict[str, dict[str, Any]] = {}
    for day in _list_prior_vald_session_dates(conn, player_name, before_date):
        start, end = report_metrics._window_bounds({"assessment_date": day})
        vald = report_metrics.aggregate_vald(conn, player_name, start, end)
        slim = _slim_session_row(day, vald)
        if int(slim.get("trial_count") or 0) <= 0:
            continue
        history.append(slim)
        raw_by_date[day] = vald

    history = _cap_prior_rows(history)
    if not history:
        return [], None, None

    current_map = _headline_map(current_vald)
    first = history[0]
    last = history[-1]
    first_day = first["session_date"]
    last_day = last["session_date"]
    vs_first = _delta_pack(
        label="earliest_lookback_session_to_current",
        from_date=first_day,
        to_date=before_date,
        previous=_headline_map(raw_by_date.get(first_day) or {}),
        current=current_map,
    )
    vs_previous = _delta_pack(
        label="most_recent_prior_session_to_current",
        from_date=last_day,
        to_date=before_date,
        previous=_headline_map(raw_by_date.get(last_day) or {}),
        current=current_map,
    )
    return history, vs_first, vs_previous


def build_draft_context(conn, payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "").strip()
    if kind not in DRAFT_KINDS:
        raise DraftError("kind must be mechanical, training_focus, or best_of_day")
    player = str(payload.get("playerName") or payload.get("player_name") or "").strip()
    if len(player) < 2:
        raise DraftError("playerName is required")
    date_str = str(
        payload.get("assessmentDate") or payload.get("assessment_date") or ""
    ).strip()
    try:
        dt.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise DraftError("assessmentDate must be YYYY-MM-DD") from e

    phases = _phase_entries(
        payload.get("mechanicsPhaseNotes") or payload.get("mechanics_phase_notes")
    )
    mechanical = str(
        payload.get("mechanicalSummary") or payload.get("mechanical_summary") or ""
    ).strip()
    notes = str(
        payload.get("assessmentNotes") or payload.get("notes") or ""
    ).strip()
    prev_focus = str(
        payload.get("previousTrainingFocus")
        or payload.get("previous_training_focus")
        or ""
    ).strip()

    if kind == "mechanical" and not _has_phase_text(phases):
        raise DraftError(
            "Add phase status or captions before drafting Mechanical Observation."
        )
    if kind == "training_focus" and not (
        _has_phase_text(phases) or mechanical or notes
    ):
        raise DraftError(
            "Add phase notes, Mechanical Observation, or Assessment Notes first."
        )

    bio = report_metrics.lookup_player_directory_bio(conn, player, date_str)
    handedness = _lookup_handedness(conn, player, date_str)
    roles = _leg_roles(handedness)

    vald_raw: dict[str, Any] = {}
    vald_pack: dict[str, Any] = {
        "trial_count": 0,
        "trials": {},
        "metrics": [],
        "bilateral": [],
    }
    vald_history: list[dict[str, Any]] = []
    vald_vs_first: Optional[dict[str, Any]] = None
    vald_vs_previous: Optional[dict[str, Any]] = None
    if kind == "best_of_day":
        start, end = report_metrics._window_bounds({"assessment_date": date_str})
        vald_raw = report_metrics.aggregate_vald(conn, player, start, end)
        vald_pack = _vald_pack(vald_raw)
        if int(vald_pack.get("trial_count") or 0) <= 0:
            raise DraftError("No force-plate tests found for this player on that date.")
        vald_history, vald_vs_first, vald_vs_previous = _build_vald_history(
            conn, player, date_str, vald_raw
        )

    return {
        "kind": kind,
        "player_name": player,
        "assessment_date": date_str,
        "bio": {
            "height": bio.get("height"),
            "weight": bio.get("weight"),
            "age_display": bio.get("age_display"),
            "found": bool(bio.get("found")),
        },
        "handedness": handedness,
        "leg_roles": roles,
        "phases": phases,
        "mechanical_summary": mechanical,
        "assessment_notes": notes,
        "previous_training_focus": prev_focus,
        "vald": vald_pack,
        "vald_history": vald_history,
        "vald_vs_first": vald_vs_first,
        "vald_vs_previous": vald_vs_previous,
    }


def _user_prompt(ctx: dict[str, Any]) -> str:
    kind = ctx["kind"]
    athlete = {
        "name": ctx["player_name"],
        "date": ctx["assessment_date"],
        **ctx["bio"],
        "handedness": ctx["handedness"],
        "leg_roles": ctx["leg_roles"],
    }
    blob = json.dumps(athlete, indent=2, default=str)
    if kind == "mechanical":
        return (
            "Write Mechanical Observation as 1 short paragraph (or two if needed) "
            "from the swing-phase cards only. Weight Development, then Monitor. "
            "Do not mention force-plate tests. Do not invent what photos show beyond "
            "these captions and statuses.\n\n"
            f"Athlete:\n{blob}\n\n"
            f"Phase cards:\n{json.dumps(ctx['phases'], indent=2)}"
        )
    if kind == "training_focus":
        return (
            "Write Training Focus as a short markdown bullet list (2–6 bullets). "
            "Each bullet is a training cue tied to the notes below. Prefer Development "
            "phases, then Monitor, then Mechanical Observation, then Assessment Notes. "
            "If previous_training_focus is present, rewrite that list for this visit "
            "instead of ignoring it. Do not add force-plate programming unless those "
            "notes already mention force, landing, or a specific leg.\n\n"
            f"Athlete:\n{blob}\n\n"
            f"Phase cards:\n{json.dumps(ctx['phases'], indent=2)}\n\n"
            f"Mechanical Observation:\n{ctx['mechanical_summary'] or '(none)'}\n\n"
            f"Assessment Notes:\n{ctx['assessment_notes'] or '(none)'}\n\n"
            f"Previous Training Focus:\n{ctx['previous_training_focus'] or '(none)'}"
        )
    roles = ctx.get("leg_roles")
    role_line = (
        f"Use right (drive) / left (landing) phrasing for this {roles['hitter']} "
        f"hitter (drive={roles['drive']}, landing/blocking={roles['landing']}). "
        "Prefer more landing force on the stride/landing leg; when that is inverted "
        "or a ~10%+ side gap is provided, flag it naturally without explaining the rule."
        if roles
        else "Handedness is unknown — do not assign drive vs landing legs."
    )
    history = ctx.get("vald_history") or []
    growth_block = ""
    if history:
        growth_block = (
            "Prior force-plate calendar days for this athlete (same player name in the "
            "force-plate DB, last ~12 months, compact headline KPIs only — includes "
            "weekly tests between formal HEAT visits when present). Precomputed deltas "
            "cover earliest lookback session → now and most recent prior session → now. "
            "Weave notable growth or regression into the same 1–3 paragraphs. "
            "Do not invent sessions or numbers.\n\n"
            f"Prior sessions:\n{json.dumps(history, indent=2, default=str)}\n\n"
            f"Deltas vs earliest lookback session:\n"
            f"{json.dumps(ctx.get('vald_vs_first'), indent=2, default=str)}\n\n"
            f"Deltas vs most recent prior session:\n"
            f"{json.dumps(ctx.get('vald_vs_previous'), indent=2, default=str)}\n\n"
        )
    return (
        "Write the Force-Plate Metrics Overall Summary as 1–3 narrative paragraphs, "
        "paste-ready (this is the single summary box — not one paragraph per test, "
        "and no 'Comprehensive Cross-Test Summary' header). Open with who the athlete "
        "is (age/height/weight/handedness when provided) and how the force-plate tests "
        "present today (isometric mid-thigh pull, hop, countermovement jump, squat "
        "jump — only those with trials) form one profile. Tie left/right gaps to drive "
        "vs landing/blocking when handedness is known. End with training priorities in "
        "order of importance.\n\n"
        f"{role_line}\n\n"
        f"{growth_block}"
        f"Athlete:\n{blob}\n\n"
        f"Force-plate session (current day):\n"
        f"{json.dumps(ctx['vald'], indent=2, default=str)}"
    )


def _api_key() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def _model_name() -> str:
    return (os.environ.get("HEAT_DRAFT_MODEL") or DEFAULT_MODEL).strip()


def call_anthropic(ctx: dict[str, Any]) -> str:
    key = _api_key()
    if not key:
        raise DraftError(
            "AI drafts are not configured (missing ANTHROPIC_API_KEY).",
            status=503,
        )
    body = {
        "model": _model_name(),
        "max_tokens": 1200,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _user_prompt(ctx)}],
    }
    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as e:
        logger.warning("Anthropic request failed: %s", e)
        raise DraftError("Could not reach the draft service. Try again.", status=502) from e
    if resp.status_code >= 400:
        logger.warning(
            "Anthropic HTTP %s: %s",
            resp.status_code,
            (resp.text or "")[:300],
        )
        raise DraftError("The draft service returned an error. Try again.", status=502)
    try:
        data = resp.json()
    except ValueError as e:
        raise DraftError("The draft service returned an unreadable response.", status=502) from e
    parts = data.get("content") or []
    texts = [
        str(p.get("text") or "").strip()
        for p in parts
        if isinstance(p, dict) and p.get("type") == "text"
    ]
    text = "\n\n".join(t for t in texts if t).strip()
    if not text:
        raise DraftError("The draft service returned empty text.", status=502)
    return text


def draft_summary(conn, payload: dict[str, Any]) -> dict[str, str]:
    ctx = build_draft_context(conn, payload)
    logger.info(
        "draft-summaries kind=%s player=%s date=%s prior_visits=%s",
        ctx["kind"],
        ctx["player_name"],
        ctx["assessment_date"],
        len(ctx.get("vald_history") or []),
    )
    text = call_anthropic(ctx)
    return {"kind": ctx["kind"], "text": text}
