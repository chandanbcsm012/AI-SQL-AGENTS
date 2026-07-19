"""Human review queue backed by the `review_queue` table in app.db.

Used by the Human Review Agent (enqueue) and by the CLI / Streamlit widget
(list_pending, decide) to close the human-in-the-loop.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "app.db"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def enqueue(
    trace_id: str,
    user_query_masked: str,
    sql_attempts: list[dict],
    schema_context: list[dict],
    db_path: Path | None = None,
) -> int:
    a1 = sql_attempts[0] if len(sql_attempts) > 0 else {}
    a2 = sql_attempts[1] if len(sql_attempts) > 1 else {}

    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO review_queue (
                trace_id, user_query_masked,
                sql_attempt_1, sql_error_1,
                sql_attempt_2, sql_error_2,
                schema_context, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                trace_id,
                user_query_masked,
                a1.get("sql"),
                a1.get("error"),
                a2.get("sql"),
                a2.get("error"),
                json.dumps(schema_context),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_pending(db_path: Path | None = None) -> list[sqlite3.Row]:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM review_queue WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()


def get(review_id: int, db_path: Path | None = None) -> sqlite3.Row | None:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM review_queue WHERE review_id = ?", (review_id,)
        ).fetchone()
    finally:
        conn.close()


def decide(
    review_id: int,
    approved: bool,
    reviewer: str,
    decision_sql: str | None = None,
    decision_reason: str | None = None,
    db_path: Path | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE review_queue
            SET status = ?, reviewer = ?, decision_sql = ?, decision_reason = ?, decided_at = ?
            WHERE review_id = ?
            """,
            (
                "approved" if approved else "rejected",
                reviewer,
                decision_sql,
                decision_reason,
                datetime.now(timezone.utc).isoformat(),
                review_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
