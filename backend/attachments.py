"""Trainer visual attachments (images) for assessment PDFs."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import gcs_upload

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_BYTES = 4 * 1024 * 1024  # 4 MB per file
# One image × 5 mechanics phases + several optional extras
MAX_FILES = 24
MAX_PHOTOS_PER_MECHANICS_PHASE = 1
MECHANICS_PHASE_SLOTS = (
    "load_phase",
    "load_position",
    "stride_phase",
    "launch_position",
    "impact",
)


_CROP_COL: dict[str, Any] = {"probed": False, "ok": False}


def _has_crop_column(conn) -> bool:
    if _CROP_COL["probed"]:
        return bool(_CROP_COL["ok"])
    _CROP_COL["probed"] = True
    _CROP_COL["ok"] = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SHOW COLUMNS FROM assessment_attachments LIKE 'crop_json'"
            )
            _CROP_COL["ok"] = bool(cur.fetchone())
    except Exception as e:
        logger.warning("assessment_attachments crop_json probe failed: %s", e)
    return bool(_CROP_COL["ok"])


def parse_crop(raw: Any) -> Optional[dict[str, float]]:
    """Normalized crop box {x, y, w, h} in 0–1 of the EXIF-corrected image."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(raw, dict):
        return None
    try:
        x = float(raw.get("x"))
        y = float(raw.get("y"))
        w = float(raw.get("w"))
        h = float(raw.get("h"))
    except (TypeError, ValueError):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        return None
    if x + w > 1.0001 or y + h > 1.0001:
        w = min(w, 1.0 - x)
        h = min(h, 1.0 - y)
        if w <= 0 or h <= 0:
            return None
    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "w": round(w, 4),
        "h": round(h, 4),
    }


def dump_crop(crop: Any) -> Optional[str]:
    parsed = parse_crop(crop)
    if not parsed:
        return None
    return json.dumps(parsed, separators=(",", ":"))


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip()).strip("_").lower()
    return s or "player"


def _attachments_local_dir(assessment_id: int) -> Path:
    base = Path(os.environ.get("REPORT_LOCAL_DIR", "reports"))
    path = base / "attachments" / str(assessment_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_attachments(conn, assessment_id: int) -> list[dict[str, Any]]:
    cols = (
        "attachment_id, assessment_id, slot, caption, gcs_uri, "
        "local_path, content_type, sort_order, created_at"
    )
    if _has_crop_column(conn):
        cols += ", crop_json"
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                f"""
                SELECT {cols}
                FROM assessment_attachments
                WHERE assessment_id = %s
                ORDER BY sort_order ASC, attachment_id ASC
                """,
                (assessment_id,),
            )
            rows = list(cur.fetchall() or [])
    except Exception as e:
        logger.warning("list_attachments failed (table missing?): %s", e)
        return []
    for row in rows:
        row["crop"] = parse_crop(row.get("crop_json"))
    return rows


def save_attachment_row(
    conn,
    *,
    assessment_id: int,
    slot: str,
    caption: Optional[str],
    gcs_uri: str,
    local_path: Optional[str],
    content_type: Optional[str],
    sort_order: int,
    crop: Any = None,
) -> int:
    crop_json = dump_crop(crop) if _has_crop_column(conn) else None
    with conn.cursor() as cur:
        if _has_crop_column(conn):
            cur.execute(
                """
                INSERT INTO assessment_attachments
                    (assessment_id, slot, caption, gcs_uri, local_path,
                     content_type, sort_order, crop_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    assessment_id,
                    (slot or "other")[:64],
                    (caption or "").strip()[:512] or None,
                    gcs_uri,
                    local_path,
                    content_type,
                    sort_order,
                    crop_json,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO assessment_attachments
                    (assessment_id, slot, caption, gcs_uri, local_path, content_type, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    assessment_id,
                    (slot or "other")[:64],
                    (caption or "").strip()[:512] or None,
                    gcs_uri,
                    local_path,
                    content_type,
                    sort_order,
                ),
            )
        conn.commit()
        return int(cur.lastrowid)


def delete_attachments(
    conn,
    assessment_id: int,
    attachment_ids: Optional[list[int]] = None,
) -> list[int]:
    """
    Remove attachment rows (plus local/GCS copies) for an assessment.

    Passing attachment_ids=None deletes every attachment on the assessment.
    Returns the ids that were deleted.
    """
    rows = list_attachments(conn, assessment_id)
    if attachment_ids is not None:
        wanted = {int(i) for i in attachment_ids}
        rows = [r for r in rows if int(r["attachment_id"]) in wanted]
    if not rows:
        return []

    deleted: list[int] = []
    for row in rows:
        local = row.get("local_path")
        if local:
            try:
                Path(local).unlink(missing_ok=True)
            except Exception:
                logger.warning("Could not remove local attachment %s", local)
        gcs_upload.delete_object(row.get("gcs_uri") or "")
        deleted.append(int(row["attachment_id"]))

    placeholders = ", ".join(["%s"] * len(deleted))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            DELETE FROM assessment_attachments
            WHERE assessment_id = %s AND attachment_id IN ({placeholders})
            """,
            (assessment_id, *deleted),
        )
        conn.commit()
    return deleted


def reorder_attachments(conn, assessment_id: int, ordered_ids: list[int]) -> list[int]:
    """
    Persist a new global sort_order from a permutation of attachment ids.

    Returns the ids in the applied order.
    """
    rows = list_attachments(conn, assessment_id)
    existing = [int(r["attachment_id"]) for r in rows]
    wanted = [int(i) for i in ordered_ids]
    if sorted(wanted) != sorted(existing):
        raise ValueError("ordered ids must include every attachment exactly once")
    with conn.cursor() as cur:
        for idx, aid in enumerate(wanted):
            cur.execute(
                """
                UPDATE assessment_attachments
                SET sort_order = %s
                WHERE assessment_id = %s AND attachment_id = %s
                """,
                (idx, assessment_id, aid),
            )
        conn.commit()
    return wanted


def update_attachment_captions(
    conn,
    assessment_id: int,
    captions: dict[int, str],
) -> list[int]:
    """Update caption text for attachments that belong to this assessment."""
    if not captions:
        return []
    rows = list_attachments(conn, assessment_id)
    known = {int(r["attachment_id"]) for r in rows}
    updated: list[int] = []
    with conn.cursor() as cur:
        for raw_id, raw_caption in captions.items():
            aid = int(raw_id)
            if aid not in known:
                continue
            text = (raw_caption or "").strip()[:512] or None
            cur.execute(
                """
                UPDATE assessment_attachments
                SET caption = %s
                WHERE assessment_id = %s AND attachment_id = %s
                """,
                (text, assessment_id, aid),
            )
            updated.append(aid)
        conn.commit()
    return updated


def update_attachment_crops(
    conn,
    assessment_id: int,
    crops: dict[int, Any],
) -> list[int]:
    """Update crop boxes for attachments that belong to this assessment."""
    if not crops or not _has_crop_column(conn):
        return []
    rows = list_attachments(conn, assessment_id)
    known = {int(r["attachment_id"]) for r in rows}
    updated: list[int] = []
    with conn.cursor() as cur:
        for raw_id, raw_crop in crops.items():
            aid = int(raw_id)
            if aid not in known:
                continue
            cur.execute(
                """
                UPDATE assessment_attachments
                SET crop_json = %s
                WHERE assessment_id = %s AND attachment_id = %s
                """,
                (dump_crop(raw_crop), assessment_id, aid),
            )
            updated.append(aid)
        conn.commit()
    return updated


def store_image_bytes(
    *,
    assessment_id: int,
    player_name: str,
    assessment_date: Any,
    raw: bytes,
    content_type: str,
    slot: str = "other",
    original_name: str = "",
) -> tuple[str, str]:
    """
    Write image to local cache + GCS.

    Returns (gcs_or_local_uri, local_path).
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_CONTENT_TYPES:
        raise ValueError(f"Unsupported image type: {content_type}")
    if not raw:
        raise ValueError("Empty file")
    if len(raw) > MAX_BYTES:
        raise ValueError(f"File exceeds {MAX_BYTES // (1024 * 1024)} MB limit")

    ext = ALLOWED_CONTENT_TYPES[ct]
    if original_name and "." in original_name:
        maybe = "." + original_name.rsplit(".", 1)[-1].lower()
        if maybe in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg" if maybe == ".jpeg" else maybe

    if hasattr(assessment_date, "strftime"):
        date_part = assessment_date.strftime("%Y-%m-%d")
    else:
        date_part = str(assessment_date or "unknown")[:10]

    fname = f"{slot}_{uuid.uuid4().hex[:10]}{ext}"
    local_path = str(_attachments_local_dir(assessment_id) / fname)
    Path(local_path).write_bytes(raw)

    object_name = f"{_slug(player_name)}/{date_part}_{assessment_id}/context/{fname}"
    uri = None
    try:
        uri = gcs_upload.upload_bytes(raw, object_name=object_name, content_type=ct)
    except Exception:
        logger.exception("GCS image upload failed; keeping local file %s", local_path)

    if not uri:
        uri = f"file://{local_path}"
    return uri, local_path


def load_image_bytes(att: dict[str, Any]) -> Optional[bytes]:
    """Load attachment bytes from local cache or GCS (authenticated)."""
    local = att.get("local_path")
    if local and Path(local).is_file():
        return Path(local).read_bytes()

    uri = (att.get("gcs_uri") or "").strip()
    if uri.startswith("file://"):
        path = uri[7:]
        if Path(path).is_file():
            return Path(path).read_bytes()
        return None

    bucket, key = gcs_upload.parse_gcs_uri(uri)
    if bucket and key:
        try:
            return gcs_upload.download_bytes(uri)
        except Exception:
            logger.exception("Failed to download attachment %s", uri)
            return None

    if uri.startswith("https://") or uri.startswith("http://"):
        try:
            import requests

            resp = requests.get(uri, timeout=30)
            resp.raise_for_status()
            return resp.content
        except Exception:
            logger.exception("Failed to download attachment %s", uri)
            return None

    return None


def attachments_for_pdf(conn, assessment_id: int) -> list[dict[str, Any]]:
    """Slim list with image bytes ready for ReportLab."""
    out: list[dict[str, Any]] = []
    for att in list_attachments(conn, assessment_id):
        data = load_image_bytes(att)
        if not data:
            continue
        out.append(
            {
                "slot": att.get("slot") or "other",
                "caption": att.get("caption") or "",
                "content_type": att.get("content_type") or "image/png",
                "bytes": data,
                "crop": att.get("crop"),
            }
        )
    return out
