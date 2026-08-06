"""Upload HEAT PDFs and trainer images to Google Cloud Storage."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _bucket_and_prefix() -> tuple[Optional[str], str]:
    bucket_name = os.environ.get("HEAT_GCS_BUCKET", "").strip()
    prefix = os.environ.get("HEAT_GCS_PREFIX", "heat-assessments/").strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket_name or None, prefix


def _with_prefix(object_name: str, prefix: str) -> str:
    if object_name.startswith(prefix):
        return object_name
    return f"{prefix}{object_name.lstrip('/')}"


def upload_pdf(local_path: str, object_name: Optional[str] = None) -> Optional[str]:
    """
    Upload a PDF to HEAT_GCS_BUCKET.

    Returns a public-style HTTPS URL when possible, else gs:// URI.
    Returns None if bucket is not configured.
    """
    bucket_name, prefix = _bucket_and_prefix()
    if not bucket_name:
        logger.info("HEAT_GCS_BUCKET not set; skipping upload for %s", local_path)
        return None

    path = Path(local_path)
    if object_name is None:
        object_name = f"{prefix}{path.name}"
    else:
        object_name = _with_prefix(object_name, prefix)

    try:
        from google.cloud import storage
    except ImportError as e:
        raise RuntimeError("google-cloud-storage is not installed") from e

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(path), content_type="application/pdf")
    logger.info("Uploaded %s to gs://%s/%s", local_path, bucket_name, object_name)

    return f"https://storage.googleapis.com/{bucket_name}/{object_name}"


def delete_object(uri: str) -> bool:
    """Best-effort delete of a previously uploaded object by https:// or gs:// URI."""
    uri = (uri or "").strip()
    if not uri:
        return False

    bucket_name = None
    object_name = None
    if uri.startswith("gs://"):
        bucket_name, _, object_name = uri[len("gs://"):].partition("/")
    elif uri.startswith("https://storage.googleapis.com/"):
        rest = uri[len("https://storage.googleapis.com/"):]
        bucket_name, _, object_name = rest.partition("/")
    if not bucket_name or not object_name:
        return False

    try:
        from google.cloud import storage

        client = storage.Client()
        client.bucket(bucket_name).blob(object_name).delete()
        logger.info("Deleted gs://%s/%s", bucket_name, object_name)
        return True
    except Exception:
        logger.exception("Failed deleting %s", uri)
        return False


def upload_bytes(
    data: bytes,
    *,
    object_name: str,
    content_type: str = "application/octet-stream",
) -> Optional[str]:
    """Upload raw bytes (e.g. trainer images) to HEAT_GCS_BUCKET."""
    bucket_name, prefix = _bucket_and_prefix()
    if not bucket_name:
        logger.info("HEAT_GCS_BUCKET not set; skipping bytes upload for %s", object_name)
        return None

    object_name = _with_prefix(object_name, prefix)

    try:
        from google.cloud import storage
    except ImportError as e:
        raise RuntimeError("google-cloud-storage is not installed") from e

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(data, content_type=content_type)
    logger.info("Uploaded %s bytes to gs://%s/%s", len(data), bucket_name, object_name)
    return f"https://storage.googleapis.com/{bucket_name}/{object_name}"
