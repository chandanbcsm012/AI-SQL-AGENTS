"""Username/password authentication and per-user table authorization.

Not production-grade auth (no sessions/tokens, no lockout, no MFA) -- this
is a demo-scoped implementation: PBKDF2-hashed passwords in SQLite, a login
held in Streamlit's session_state, and table-level visibility rules:

- A table is "public" if no row for it exists in `table_ownership` (this is
  true for every seeded table: customer, product, orders, order_item,
  review_queue). Public tables are visible to everyone.
- A table created afterwards (via the SQL Editor or Import tab) is owned by
  whichever user created it and is visible only to that user.
- A superuser (`is_superuser=1`) always sees every table, public or not.
"""
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "db" / "app.db"
PBKDF2_ITERATIONS = 200_000

# Metadata tables are never part of the queryable/browsable schema, for
# anyone -- they hold credentials and ownership bookkeeping, not app data.
SYSTEM_TABLES = {"app_user", "table_ownership"}


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS).hex()


ROLES = ("viewer", "editor", "admin")


def create_user(
    username: str, password: str, role: str = "editor", db_path: Path | None = None
) -> None:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    salt = os.urandom(16)
    password_hash = _hash_password(password, salt)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO app_user (username, password_hash, salt, role, is_superuser, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                username,
                password_hash,
                salt.hex(),
                role,
                int(role == "admin"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def authenticate(username: str, password: str, db_path: Path | None = None) -> dict | None:
    """Returns {"username", "role", "is_superuser"} on success, else None.
    is_superuser is kept alongside role (role == "admin") for code that
    predates the role tiers and only checks the boolean."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM app_user WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return None
        if _hash_password(password, bytes.fromhex(row["salt"])) != row["password_hash"]:
            return None
        return {
            "username": row["username"],
            "role": row["role"],
            "is_superuser": bool(row["is_superuser"]),
        }
    finally:
        conn.close()


def ensure_default_users(db_path: Path | None = None) -> None:
    """Seeds default demo accounts, one per role, the first time app_user
    is empty. Change these passwords before using this anywhere but a
    local demo."""
    conn = _connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM app_user").fetchone()["n"]
    finally:
        conn.close()
    if count == 0:
        create_user("admin", "admin123", role="admin", db_path=db_path)
        create_user("alice", "alice123", role="editor", db_path=db_path)
        create_user("bob", "bob123", role="editor", db_path=db_path)
        create_user("carol", "carol123", role="viewer", db_path=db_path)


def record_table_owner(table_name: str, owner: str, db_path: Path | None = None) -> None:
    if table_name in SYSTEM_TABLES:
        return
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO table_ownership (table_name, owner, created_at) VALUES (?, ?, ?)",
            (table_name, owner, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_table_owner(table_name: str, db_path: Path | None = None) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT owner FROM table_ownership WHERE table_name = ?", (table_name,)
        ).fetchone()
        return row["owner"] if row else None
    finally:
        conn.close()


def visible_tables(
    username: str, is_superuser: bool, db_path: Path | None = None
) -> set[str] | None:
    """Returns None for a superuser (no restriction -- sees everything,
    including system tables). Otherwise returns the set of table names this
    user may see: every public table, plus tables they personally own."""
    if is_superuser:
        return None

    conn = _connect(db_path)
    try:
        all_tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        owned_by_others = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM table_ownership WHERE owner != ?", (username,)
            ).fetchall()
        }
        return all_tables - owned_by_others - SYSTEM_TABLES
    finally:
        conn.close()


def can_write(role: str) -> bool:
    """Viewers are read-only everywhere: no unguarded SQL, no schema/data
    import, regardless of table ownership. Editors and admins can."""
    return role in ("editor", "admin")


def can_access_table(
    table_name: str, username: str, is_superuser: bool, db_path: Path | None = None
) -> bool:
    if is_superuser:
        return True
    if table_name in SYSTEM_TABLES:
        return False
    owner = get_table_owner(table_name, db_path=db_path)
    return owner is None or owner == username
