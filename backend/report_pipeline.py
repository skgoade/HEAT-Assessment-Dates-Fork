"""Orchestrate draft report generation for a completed assessment window."""
from __future__ import annotations

import logging
import re
from typing import Optional

import gcs_upload
import report_metrics
import report_pdf

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return s or "player"


def generate_draft_report(
    conn,
    assessment_row: dict,
    *,
    version_num: Optional[int] = None,
) -> Optional[str]:
    """
    Build metrics + PDF for a hitting_assessments row (assessment day only).

    Returns GCS HTTPS URL when upload succeeds, else a local file:// path.
    Object keys are player/date/id_vN so regenerations do not overwrite prior PDFs.
    """
    bundle = report_metrics.build_report_bundle(conn, assessment_row)
    try:
        import attachments as attachments_mod

        aid = assessment_row["assessment_id"]
        bundle["trainer_visuals"] = attachments_mod.attachments_for_pdf(conn, aid)
    except Exception:
        logger.exception("Failed loading trainer visuals for PDF")
        bundle["trainer_visuals"] = []

    local_path = report_pdf.build_pdf(bundle)

    player = assessment_row["player_name"]
    aid = assessment_row["assessment_id"]
    adate = assessment_row.get("assessment_date")
    if hasattr(adate, "strftime"):
        date_part = adate.strftime("%Y-%m-%d")
    else:
        date_part = str(adate)[:10] if adate else "unknown-date"

    if version_num is None:
        try:
            import report_versions

            with conn.cursor(dictionary=True) as cursor:
                version_num = report_versions.next_version_num(cursor, aid)
        except Exception:
            logger.exception("Could not resolve next PDF version for %s", aid)
            version_num = 1

    object_name = f"{_slug(player)}/{date_part}_{aid}_v{version_num}.pdf"
    try:
        url = gcs_upload.upload_pdf(local_path, object_name=object_name)
    except Exception as e:
        logger.exception(
            "GCS upload failed for assessment %s; keeping local PDF at %s: %s",
            aid,
            local_path,
            e,
        )
        url = None
    if url:
        return url
    return f"file://{local_path}"


def generate_and_store_report(conn, assessment_row: dict) -> Optional[dict]:
    """
    Reserve next version, build/upload PDF, persist URI + version row.

    Returns store_report_uri metadata (report_uri is browser-openable when signed).
    """
    import report_versions

    aid = assessment_row["assessment_id"]
    with conn.cursor(dictionary=True) as cursor:
        version_num = report_versions.next_version_num(cursor, aid)
    uri = generate_draft_report(conn, assessment_row, version_num=version_num)
    if not uri:
        return None
    return report_versions.store_report_uri(
        conn, aid, uri, version_num=version_num
    )
