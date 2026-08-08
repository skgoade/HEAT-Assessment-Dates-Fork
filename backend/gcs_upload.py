"""Upload HEAT PDFs and trainer images to Google Cloud Storage."""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import unquote

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


def _https_url(bucket_name: str, object_name: str) -> str:
    return f"https://storage.googleapis.com/{bucket_name}/{object_name}"


def parse_gcs_uri(uri: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse gs:// or https://storage.googleapis.com/ URI into (bucket, object)."""
    uri = (uri or "").strip()
    if not uri:
        return None, None
    if uri.startswith("gs://"):
        bucket_name, _, object_name = uri[len("gs://"):].partition("/")
        return bucket_name or None, unquote(object_name) if object_name else None
    if uri.startswith("https://storage.googleapis.com/"):
        rest = uri[len("https://storage.googleapis.com/"):]
        bucket_name, _, object_name = rest.partition("/")
        return bucket_name or None, unquote(object_name) if object_name else None
    return None, None


def _signed_url_ttl() -> timedelta:
    minutes = int(os.environ.get("HEAT_GCS_SIGNED_URL_MINUTES", "60") or "60")
    return timedelta(minutes=max(1, minutes))


def _service_account_email(credentials) -> Optional[str]:
    email = getattr(credentials, "service_account_email", None)
    if email and email != "default":
        return email
    env_email = (os.environ.get("HEAT_GCS_SIGNER_SA") or "").strip()
    if env_email:
        return env_email
    try:
        import google.auth.compute_engine._metadata as metadata

        info = metadata.get_service_account_info()
        return (info or {}).get("email")
    except Exception:
        return None


def signed_url_for_uri(uri: str) -> Optional[str]:
    """
    Return a short-lived V4 signed GET URL for a private GCS object.

    Works on Cloud Run via IAM signBlob (access token + SA email).
    Returns None if signing is unavailable (e.g. local user ADC without a key).
    """
    bucket_name, object_name = parse_gcs_uri(uri)
    if not bucket_name or not object_name:
        return None

    try:
        import google.auth
        from google.auth.transport import requests as google_auth_requests
        from google.cloud import storage
    except ImportError:
        logger.warning("google-cloud-storage / google-auth not installed; cannot sign URL")
        return None

    try:
        credentials, _project = google.auth.default()
        auth_request = google_auth_requests.Request()
        credentials.refresh(auth_request)
        sa_email = _service_account_email(credentials)
        if not sa_email or not getattr(credentials, "token", None):
            logger.info("Signed URL skipped: no service account email/token available")
            return None

        client = storage.Client(credentials=credentials)
        blob = client.bucket(bucket_name).blob(object_name)
        return blob.generate_signed_url(
            version="v4",
            expiration=_signed_url_ttl(),
            method="GET",
            service_account_email=sa_email,
            access_token=credentials.token,
        )
    except Exception:
        logger.exception("Failed to sign GCS URL for %s", uri)
        return None


def accessible_url(uri: Optional[str]) -> Optional[str]:
    """
    Prefer a signed HTTPS URL for browser opens; fall back to the stored URI.
    Local file:// paths are returned unchanged.
    """
    if not uri:
        return None
    if uri.startswith("file://"):
        return uri
    signed = signed_url_for_uri(uri)
    return signed or uri


def upload_pdf(local_path: str, object_name: Optional[str] = None) -> Optional[str]:
    """
    Upload a PDF to HEAT_GCS_BUCKET.

    Returns a stable https://storage.googleapis.com/... URI (bucket may be private;
    use accessible_url / signed_url_for_uri for browser links).
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

    return _https_url(bucket_name, object_name)


def delete_object(uri: str) -> bool:
    """Best-effort delete of a previously uploaded object by https:// or gs:// URI."""
    uri = (uri or "").strip()
    if not uri:
        return False

    bucket_name, object_name = parse_gcs_uri(uri)
    if not bucket_name or not object_name:
        return False

    try:
        from google.cloud import storage

        client = storage.Client()
        client.bucket(bucket_name).blob(object_name).delete()
        logger.info("Deleted gs://%s/%s", bucket_name, object_name)
    except Exception:
        logger.exception("Failed deleting %s", uri)
        return False
    return True


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
    return _https_url(bucket_name, object_name)
