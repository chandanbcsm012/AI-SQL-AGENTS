"""One-shot helper: (re)creates app.db from schema.sql + seed_data.sql, then
seeds the default admin/demo user accounts."""
import sqlite3
import sys
from pathlib import Path

DB_DIR = Path(__file__).parent
DB_PATH = DB_DIR / "app.db"

sys.path.insert(0, str(DB_DIR.parent))


def init_db(db_path: Path = DB_PATH, reset: bool = True) -> None:
    if reset and db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript((DB_DIR / "schema.sql").read_text())
        conn.executescript((DB_DIR / "seed_data.sql").read_text())
        conn.commit()
    finally:
        conn.close()

    from auth import ensure_default_users

    ensure_default_users(db_path=db_path)


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
