"""Schema Retriever Agent (spec section 3.2).

Introspects the live SQLite schema via sqlite_master / PRAGMA table_info and
ranks tables by relevance to the question. Two ranking backends:

- "lexical" (default): keyword overlap, zero dependencies, fine for this
  demo's handful of tables.
- "semantic" (SCHEMA_RETRIEVAL_BACKEND=semantic): embedding similarity via
  Qdrant (see semantic_schema.py), for schemas large enough that keyword
  overlap stops being reliable. Falls back to lexical automatically if
  Qdrant/the embedding model isn't reachable.
"""
import os
import re
import sqlite3
from pathlib import Path

import auth
from middleware.tracing import traced_node
from resilience import resilient_node

DB_PATH = Path(__file__).parent.parent / "db" / "app.db"
SCHEMA_RETRIEVAL_BACKEND = os.getenv("SCHEMA_RETRIEVAL_BACKEND", "lexical")


def _introspect_schema(db_path: Path | None = None, allowed_tables: set[str] | None = None) -> list[dict]:
    """allowed_tables=None means unrestricted (superuser, or no logged-in
    user at all); otherwise only tables in that set are returned."""
    conn = sqlite3.connect(db_path or DB_PATH)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if allowed_tables is None or row[0] in allowed_tables
        ]
        schema = []
        for table in tables:
            columns = [
                {"name": col[1], "type": col[2]}
                for col in conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            schema.append({"table": table, "columns": columns})
        return schema
    finally:
        conn.close()


def _rank_tables(question: str, schema: list[dict], top_k: int = 4) -> list[dict]:
    words = set(re.findall(r"[a-z0-9_]+", question.lower()))

    def score(entry: dict) -> int:
        haystack = {entry["table"].lower()} | {c["name"].lower() for c in entry["columns"]}
        return sum(1 for w in words if any(w in h or h in w for h in haystack))

    ranked = sorted(schema, key=score, reverse=True)
    scored = [t for t in ranked if score(t) > 0]
    return (scored or schema)[:top_k] if scored else schema[:top_k]


def schema_retriever(state: dict) -> dict:
    username = state.get("username")
    allowed = (
        None
        if username is None
        else auth.visible_tables(username, state.get("is_superuser", False))
    )
    schema = _introspect_schema(allowed_tables=allowed)
    question = state.get("user_query_masked") or state.get("user_query_raw", "")

    ranked = None
    if SCHEMA_RETRIEVAL_BACKEND == "semantic":
        from semantic_schema import semantic_rank_tables

        ranked = semantic_rank_tables(question, schema)

    state["schema_context"] = ranked if ranked is not None else _rank_tables(question, schema)
    return state


invoke = resilient_node()(traced_node(schema_retriever))
