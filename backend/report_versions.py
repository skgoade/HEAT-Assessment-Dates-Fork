"""PDF report version history for hitting assessments."""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import gcs_upload

logger = logging.getLogger(__name__)


def next_version_num(cursor, assessment_id: int) -> int:
    """Return the next 1-based version number for this assessment."""
    cursor.execute(
        """
        SELECT COALESCE(MAX(version_num), 0) + 1 AS next_num
        FROM assessment_report_versions
        WHERE assessment_id = %s
        """,
        (assessment_id,),
    )
    row = cursor.fetchone()
    if isinstance(row, dict):
        return int(row.get("next_num") or 1)
    return int(row[0] if row else 1)


def record_version(
    cursor,
    *,
    assessment_id: int,
    version_num: int,
    gcs_uri: str,
) -> None:
    """Insert a version row (caller commits)."""
    cursor.execute(
        """
        INSERT INTO assessment_report_versions
            (assessment_id, version_num, gcs_uri)
        VALUES (%s, %s, %s)
        """,
        (assessment_id, version_num, gcs_uri),
    )


def update_latest_uri(cursor, assessment_id: int, gcs_uri: str) -> None:
    """Point hitting_assessments.report_gcs_uri at the latest PDF."""
    cursor.execute(
        """
        UPDATE hitting_assessments
        SET report_gcs_uri = %s
        WHERE assessment_id = %s
        """,
        (gcs_uri, assessment_id),
    )


def store_report_uri(
    connection,
    assessment_id: int,
    uri: Optional[str],
    *,
    version_num: Optional[int] = None,
) -> Optional[dict]:
    """
    Persist latest URI + a new version row when uri is a GCS/https object.

    Local file:// paths only update report_gcs_uri (no version row).
    Pass version_num when the PDF object was already named with that version.
    Returns version metadata dict or None.
    """
    if not uri:
        return None

    with connection.cursor(dictionary=True) as cursor:
        if uri.startswith("file://"):
            update_latest_uri(cursor, assessment_id, uri)
            connection.commit()
            return {
                "version_num": None,
                "gcs_uri": uri,
                "report_uri": uri,
                "report_url": uri,
            }

        if version_num is None:
            version_num = next_version_num(cursor, assessment_id)
        try:
            record_version(
                cursor,
                assessment_id=assessment_id,
                version_num=version_num,
                gcs_uri=uri,
            )
        except Exception:
            # Table may not exist yet on older DBs — still save latest URI.
            logger.exception(
                "Could not insert assessment_report_versions for %s; "
                "saving report_gcs_uri only",
                assessment_id,
            )
            connection.rollback()
            with connection.cursor() as c2:
                update_latest_uri(c2, assessment_id, uri)
                connection.commit()
            accessible = gcs_upload.accessible_url(uri) or uri
            return {
                "version_num": None,
                "gcs_uri": uri,
                "report_uri": accessible,
                "report_url": accessible,
            }

        update_latest_uri(cursor, assessment_id, uri)
        connection.commit()

    accessible = gcs_upload.accessible_url(uri) or uri
    return {
        "version_num": version_num,
        "gcs_uri": uri,
        "report_uri": accessible,
        "report_url": accessible,
    }


def list_versions(connection, assessment_id: int) -> List[dict[str, Any]]:
    """
    Return version history newest-first.

    If the versions table is empty/missing but report_gcs_uri is set, synthesize v1
    so older assessments still show one openable link.
    """
    versions: List[dict[str, Any]] = []
    try:
        with connection.cursor(dictionary=True) as cursor:
            try:
                cursor.execute(
                    """
                    SELECT version_id, assessment_id, version_num, gcs_uri, created_at
                    FROM assessment_report_versions
                    WHERE assessment_id = %s
                    ORDER BY version_num DESC
                    """,
                    (assessment_id,),
                )
                versions = list(cursor.fetchall() or [])
            except Exception:
                logger.warning(
                    "assessment_report_versions unavailable; falling back to "
                    "hitting_assessments.report_gcs_uri for %s",
                    assessment_id,
                )
                try:
                    connection.rollback()
                except Exception:
                    pass

            if not versions:
                with connection.cursor(dictionary=True) as cursor2:
                    cursor2.execute(
                        """
                        SELECT report_gcs_uri
                        FROM hitting_assessments
                        WHERE assessment_id = %s
                        """,
                        (assessment_id,),
                    )
                    row = cursor2.fetchone()
                uri = (row or {}).get("report_gcs_uri") if row else None
                if uri:
                    versions = [
                        {
                            "version_id": None,
                            "assessment_id": assessment_id,
                            "version_num": 1,
                            "gcs_uri": uri,
                            "created_at": None,
                            "legacy": True,
                        }
                    ]
    except Exception:
        logger.exception("list_versions failed for assessment %s", assessment_id)
        return []

    out = []
    for v in versions:
        uri = v.get("gcs_uri")
        created = v.get("created_at")
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d %H:%M:%S")
        out.append(
            {
                "version_id": v.get("version_id"),
                "assessment_id": assessment_id,
                "version_num": v.get("version_num"),
                "gcs_uri": uri,
                "report_uri": gcs_upload.accessible_url(uri) or uri,
                "report_url": gcs_upload.accessible_url(uri) or uri,
                "created_at": created,
                "legacy": bool(v.get("legacy")),
            }
        )
    return out
