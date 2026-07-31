"""Shared MySQL connection helpers."""
from __future__ import annotations

import logging
import os

import mysql.connector
from mysql.connector.constants import ClientFlag

logger = logging.getLogger(__name__)


def _allow_clear_password_via_auth_proxy() -> None:
    """
    Cloud SQL Auth Proxy encrypts the path to Cloud SQL, but the local hop
    (127.0.0.1:3306) is plain TCP. mysql-connector still refuses
    mysql_clear_password (used by some IAM / Cloud SQL users) unless the
    *client library* thinks SSL is on.

    For local proxy use only, relax that client-side check.
    """
    from mysql.connector.plugins.mysql_clear_password import (
        MySQLClearPasswordAuthPlugin,
    )

    if getattr(MySQLClearPasswordAuthPlugin, "_heat_proxy_patch", False):
        return

    def _requires_ssl(self) -> bool:  # noqa: ANN001
        return False

    MySQLClearPasswordAuthPlugin.requires_ssl = property(_requires_ssl)  # type: ignore[method-assign]
    MySQLClearPasswordAuthPlugin._heat_proxy_patch = True  # type: ignore[attr-defined]


def _db_config() -> dict:
    """Read connection settings from the environment at connect time."""
    host = (os.environ.get("DB_HOST") or "").strip() or "127.0.0.1"
    return {
        "host": host,
        "user": os.environ.get("DB_USER"),
        "password": os.environ.get("DB_PASS") or "",
        "database": os.environ.get("DB_NAME_PROD", "PlayerDev"),
        "port": int(os.environ.get("DB_PORT", 3306)),
        "connect_timeout": 10,
        "use_pure": True,
        "client_flags": [ClientFlag.SSL],
        "ssl_ca": "",
        "ssl_disabled": False,
        "ssl_verify_cert": False,
        "ssl_verify_identity": False,
    }


def get_db_connection():
    """Create and return a database connection."""
    cfg = _db_config()
    host = (cfg.get("host") or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        _allow_clear_password_via_auth_proxy()

    try:
        return mysql.connector.connect(**cfg)
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        raise
