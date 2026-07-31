"""Upload HEAT PDFs to Google Cloud Storage."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def upload_pdf(local_path: str, object_name: Optional[str] = None) -> Optional[str]:
    """
    Upload a PDF to HEAT_GCS_BUCKET.

    Returns a public-style HTTPS URL when possible, else gs:// URI.
    Returns None if bucket is not configured.
    """
    bucket_name = os.environ.get("HEAT_GCS_BUCKET", "").strip()
    if not bucket_name:
        logger.info("HEAT_GCS_BUCKET not set; skipping upload for %s", local_path)
        return None

    prefix = os.environ.get("HEAT_GCS_PREFIX", "heat-assessments/").strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    # Prefer caller-supplied readable keys like:
    #   jason_peele/2026-01-12_2.pdf
    path = Path(local_path)
    if object_name is None:
        object_name = f"{prefix}{path.name}"
    elif not object_name.startswith(prefix):
        object_name = f"{prefix}{object_name.lstrip('/')}"

    try:
        from google.cloud import storage
    except ImportError as e:
        raise RuntimeError("google-cloud-storage is not installed") from e

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(path), content_type="application/pdf")
    logger.info("Uploaded %s to gs://%s/%s", local_path, bucket_name, object_name)

    # Prefer HTTPS if bucket is publicly readable or via storage.googleapis.com
    return f"https://storage.googleapis.com/{bucket_name}/{object_name}"
