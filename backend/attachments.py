"""Trainer visual attachments (images) for assessment PDFs."""
from __future__ import annotations

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
MAX_FILES = 6


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip()).strip("_").lower()
    return s or "player"


def _attachments_local_dir(assessment_id: int) -> Path:
    base = Path(os.environ.get("REPORT_LOCAL_DIR", "reports"))
    path = base / "attachments" / str(assessment_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_attachments(conn, assessment_id: int) -> list[dict[str, Any]]:
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                """
                SELECT attachment_id, assessment_id, slot, caption, gcs_uri,
                       local_path, content_type, sort_order, created_at
                FROM assessment_attachments
                WHERE assessment_id = %s
                ORDER BY sort_order ASC, attachment_id ASC
                """,
                (assessment_id,),
            )
            return list(cur.fetchall() or [])
    except Exception as e:
        logger.warning("list_attachments failed (table missing?): %s", e)
        return []


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
) -> int:
    with conn.cursor() as cur:
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
    """Load attachment bytes from local cache or HTTPS/gs URI."""
    local = att.get("local_path")
    if local and Path(local).is_file():
        return Path(local).read_bytes()

    uri = (att.get("gcs_uri") or "").strip()
    if uri.startswith("file://"):
        path = uri[7:]
        if Path(path).is_file():
            return Path(path).read_bytes()
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

    if uri.startswith("gs://"):
        # gs://bucket/key
        try:
            from google.cloud import storage

            _, rest = uri.split("gs://", 1)
            bucket_name, _, key = rest.partition("/")
            client = storage.Client()
            blob = client.bucket(bucket_name).blob(key)
            return blob.download_as_bytes()
        except Exception:
            logger.exception("Failed to download gs attachment %s", uri)
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
            }
        )
    return out
