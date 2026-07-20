"""Admin helpers for schema/data import and ad-hoc SQL from the UI.

These use a normal read-write SQLite connection and are intentionally
separate from agents/sql_executor.py, which stays read-only and
guardrail-gated for the agentic NL->SQL flow. Nothing here is wired into
the LangGraph pipeline.

Every table newly created through these helpers is recorded in
table_ownership under the acting user (see auth.py), which is what makes
it private to that user (and superusers) afterwards.
"""
import sqlite3
from pathlib import Path

import pandas as pd
import sqlglot
from sqlglot import exp

import auth

DB_PATH = Path(__file__).parent / "app.db"


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _record_new_tables(conn: sqlite3.Connection, before: set[str], owner: str, db_path: Path) -> None:
    for table in _existing_tables(conn) - before:
        auth.record_table_owner(table, owner, db_path=db_path)


class AccessDeniedError(Exception):
    pass


def _check_access(sql_text: str, username: str, is_superuser: bool, db_path: Path) -> None:
    """Blocks references to tables the user doesn't own/can't see. CREATE
    TABLE is exempt -- creating a brand-new name is always allowed, and
    ownership is recorded right after it succeeds."""
    if is_superuser:
        return
    try:
        parsed = sqlglot.parse_one(sql_text, read="sqlite")
    except Exception:
        return  # let execution surface the real parse error
    if isinstance(parsed, exp.Create):
        return

    referenced = {t.name for t in parsed.find_all(exp.Table)}
    for table in referenced:
        if not auth.can_access_table(table, username, is_superuser, db_path=db_path):
            raise AccessDeniedError(f"you don't have access to table '{table}'")


def execute_script(
    sql_text: str,
    username: str,
    is_superuser: bool = False,
    db_path: Path | None = None,
    role: str = "editor",
) -> None:
    """Runs a multi-statement script (e.g. an uploaded schema.sql)."""
    if not auth.can_write(role):
        raise AccessDeniedError("your role (viewer) is read-only -- ask an editor/admin to run this")
    db_path = db_path or DB_PATH
    for statement in sqlglot.parse(sql_text, read="sqlite"):
        if statement is not None:
            _check_access(statement.sql(dialect="sqlite"), username, is_superuser, db_path)

    conn = sqlite3.connect(db_path)
    try:
        before = _existing_tables(conn)
        conn.executescript(sql_text)
        conn.commit()
        _record_new_tables(conn, before, username, db_path)
    finally:
        conn.close()


def execute_write(
    sql_text: str,
    username: str,
    is_superuser: bool = False,
    db_path: Path | None = None,
    role: str = "editor",
) -> tuple[list[str], list[dict]]:
    """Runs one statement of any kind (SELECT or DDL/DML) on a read-write
    connection. Returns (columns, rows) -- empty for statements with no
    result set."""
    if not auth.can_write(role):
        raise AccessDeniedError("your role (viewer) is read-only -- use the guarded SQL Editor mode instead")
    db_path = db_path or DB_PATH
    _check_access(sql_text, username, is_superuser, db_path)

    conn = sqlite3.connect(db_path)
    try:
        before = _existing_tables(conn)
        cursor = conn.execute(sql_text)
        conn.commit()
        _record_new_tables(conn, before, username, db_path)
        if cursor.description is None:
            return [], []
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return columns, rows
    finally:
        conn.close()


def import_csv(
    file,
    table_name: str,
    username: str,
    is_superuser: bool = False,
    if_exists: str = "append",
    db_path: Path | None = None,
    role: str = "editor",
) -> int:
    """Loads a CSV file object into `table_name`. if_exists: append|replace|fail."""
    if not auth.can_write(role):
        raise AccessDeniedError("your role (viewer) is read-only -- ask an editor/admin to import data")
    db_path = db_path or DB_PATH
    if not auth.can_access_table(table_name, username, is_superuser, db_path=db_path):
        raise AccessDeniedError(f"you don't have access to table '{table_name}'")

    df = pd.read_csv(file)
    conn = sqlite3.connect(db_path)
    try:
        before = _existing_tables(conn)
        df.to_sql(table_name, conn, if_exists=if_exists, index=False)
        _record_new_tables(conn, before, username, db_path)
        return len(df)
    finally:
        conn.close()
