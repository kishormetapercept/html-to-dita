from __future__ import annotations

import re
import ssl
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pg8000

from app.core.config import get_settings


_settings = get_settings()


_KV_DSN_RE = re.compile(r"(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)=(?P<value>\"[^\"]*\"|'[^']*'|[^\s]+)")


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
        return value[1:-1]
    return value


def _parse_kv_dsn(dsn: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for match in _KV_DSN_RE.finditer(dsn):
        key = match.group("key").strip().lower()
        value = _strip_quotes(match.group("value"))
        parts[key] = value
    return parts


def _parse_postgres_uri(value: str) -> dict[str, Any]:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("postgres_uri is empty")

    if "://" in raw:
        url = urlparse(raw)
        if url.scheme not in {"postgres", "postgresql"}:
            raise ValueError(f"Unsupported database URL scheme: {url.scheme}")

        username = unquote(url.username or "")
        password = unquote(url.password or "")
        host = url.hostname or "localhost"
        port = url.port or 5432
        database = (url.path or "").lstrip("/") or "postgres"

        query = parse_qs(url.query or "")
        sslmode = (query.get("sslmode", [""])[0] or "").strip().lower()
        connect_timeout_raw = (query.get("connect_timeout", [""])[0] or "").strip()

        return {
            "user": username,
            "password": password,
            "host": host,
            "port": int(port),
            "database": database,
            "sslmode": sslmode,
            "connect_timeout": connect_timeout_raw,
        }

    parts = _parse_kv_dsn(raw)
    return {
        "user": parts.get("user", ""),
        "password": parts.get("password", ""),
        "host": parts.get("host", "localhost"),
        "port": int(parts.get("port", "5432")),
        "database": parts.get("dbname") or parts.get("database") or "postgres",
        "sslmode": (parts.get("sslmode", "") or "").strip().lower(),
        "connect_timeout": parts.get("connect_timeout", ""),
    }


def _connect_kwargs() -> dict[str, Any]:
    parsed = _parse_postgres_uri(_settings.postgres_uri)

    kwargs: dict[str, Any] = {
        "user": parsed["user"],
        "password": parsed["password"],
        "host": parsed["host"],
        "port": parsed["port"],
        "database": parsed["database"],
    }

    connect_timeout_raw = str(parsed.get("connect_timeout") or "").strip()
    if connect_timeout_raw.isdigit():
        kwargs["timeout"] = int(connect_timeout_raw)

    sslmode = str(parsed.get("sslmode") or "").strip().lower()
    if sslmode in {"require", "verify-ca", "verify-full"}:
        kwargs["ssl_context"] = ssl.create_default_context()

    return kwargs


def _ensure_schema(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS dita_tag ("
        "key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL"
        ")"
    )


@contextmanager
def get_db():
    conn = pg8000.connect(**_connect_kwargs())
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()