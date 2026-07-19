"""SQL Executor Agent (spec section 3.7).

Runs the validated SQL against SQLite through a read-only connection, with
a query timeout enforced via SQLite's progress handler (row-limit is
already baked into the SQL by the Validator via apply_row_limit).
"""
import sqlite3
import time
from pathlib import Path

from middleware.guardrails import load_policy
from middleware.tracing import traced_node
from resilience import resilient_node

DB_PATH = Path(__file__).parent.parent / "db" / "app.db"


class QueryTimeoutError(Exception):
    pass


def _execute_readonly(sql: str, db_path: Path | None = None) -> list[dict]:
    db_path = db_path or DB_PATH
    policy = load_policy()
    timeout_seconds = policy.get("sql", {}).get("query_timeout_seconds", 5)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout_seconds)
    start = time.monotonic()

    def _progress_handler():
        return 1 if (time.monotonic() - start) > timeout_seconds else 0

    conn.set_progress_handler(_progress_handler, 1000)
    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            raise QueryTimeoutError(f"query exceeded {timeout_seconds}s timeout") from e
        raise
    finally:
        conn.close()


def sql_executor(state: dict) -> dict:
    state["execution_result"] = _execute_readonly(state["final_sql"])
    return state


invoke = resilient_node()(traced_node(sql_executor))
