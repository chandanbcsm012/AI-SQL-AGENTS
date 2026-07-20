"""Durable memory on Postgres: activity log (query history + audit trail)
and per-user preferences. See db/postgres_schema.sql for the schema and
docs/IMPLEMENTATION_PLAN.md for why this is Postgres-only while
auth/table_ownership/review_queue stay on SQLite for now.

Fails open (logs a warning, returns empty/None) if Postgres is unreachable
-- same resilience posture as middleware/cache.py and middleware/rate_limit.py.
Nothing here is on the critical path for answering a query.
"""
import logging
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger("history")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://sqlagents:sqlagents@localhost:5432/sqlagents")
_SCHEMA_PATH = Path(__file__).parent / "db" / "postgres_schema.sql"

_schema_ensured = False


def _connect(dsn: str | None = None) -> "psycopg.Connection | None":
    global _schema_ensured
    try:
        conn = psycopg.connect(dsn or DATABASE_URL, row_factory=dict_row, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError as e:
        logger.warning("history_backend_unavailable", extra={"error": str(e)})
        return None

    if not _schema_ensured:
        try:
            conn.execute(_SCHEMA_PATH.read_text())
            _schema_ensured = True
        except Exception as e:
            logger.warning("history_schema_ensure_failed", extra={"error": str(e)})
    return conn


def record_activity(
    trace_id: str,
    username: str | None,
    question: str,
    sql: str | None,
    status: str,
    tables_touched: list[str] | None = None,
    dsn: str | None = None,
) -> None:
    conn = _connect(dsn)
    if conn is None:
        return
    try:
        conn.execute(
            """
            INSERT INTO activity_log (trace_id, username, question, sql, status, tables_touched)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (trace_id, username, question, sql, status, tables_touched or []),
        )
    except Exception as e:
        logger.warning("record_activity_failed", extra={"error": str(e)})
    finally:
        conn.close()


def recent_activity(username: str, limit: int = 10, dsn: str | None = None) -> list[dict]:
    conn = _connect(dsn)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT trace_id, question, sql, status, tables_touched, created_at
            FROM activity_log
            WHERE username = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (username, limit),
        ).fetchall()
        return list(rows)
    except Exception as e:
        logger.warning("recent_activity_failed", extra={"error": str(e)})
        return []
    finally:
        conn.close()


def set_preference(username: str, key: str, value: str, dsn: str | None = None) -> None:
    conn = _connect(dsn)
    if conn is None:
        return
    try:
        conn.execute(
            """
            INSERT INTO user_preferences (username, key, value, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (username, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (username, key, value),
        )
    except Exception as e:
        logger.warning("set_preference_failed", extra={"error": str(e)})
    finally:
        conn.close()


def get_preference(username: str, key: str, default: str | None = None, dsn: str | None = None) -> str | None:
    conn = _connect(dsn)
    if conn is None:
        return default
    try:
        row = conn.execute(
            "SELECT value FROM user_preferences WHERE username = %s AND key = %s",
            (username, key),
        ).fetchone()
        return row["value"] if row else default
    except Exception as e:
        logger.warning("get_preference_failed", extra={"error": str(e)})
        return default
    finally:
        conn.close()
