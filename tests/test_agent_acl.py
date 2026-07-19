"""Verifies the Schema Retriever and SQL Validator respect per-user table
visibility (auth.visible_tables) -- a non-superuser must never see, or get
valid SQL against, another user's private table."""
import auth
from agents.schema_retriever import _introspect_schema, schema_retriever
from agents.sql_validator import sql_validator
from db import admin


def _make_private_table(temp_db, owner: str, table: str = "alice_notes") -> None:
    admin.execute_write(
        f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, note TEXT)",
        username=owner,
        is_superuser=False,
        db_path=temp_db,
    )


def test_schema_retriever_excludes_others_private_table(temp_db):
    _make_private_table(temp_db, owner="alice")

    state = {"user_query_raw": "show me alice_notes", "username": "bob", "is_superuser": False}
    result = schema_retriever(state)
    table_names = {t["table"] for t in result["schema_context"]}
    assert "alice_notes" not in table_names


def test_introspect_schema_includes_own_private_table(temp_db):
    # _introspect_schema is where ACL filtering actually happens, before
    # _rank_tables' top-k truncation -- test the filtering directly rather
    # than through the ranking heuristic, which isn't part of ACL behavior.
    _make_private_table(temp_db, owner="alice")

    allowed = auth.visible_tables("alice", is_superuser=False, db_path=temp_db)
    schema = _introspect_schema(db_path=temp_db, allowed_tables=allowed)
    assert "alice_notes" in {t["table"] for t in schema}


def test_introspect_schema_unrestricted_without_username(temp_db):
    _make_private_table(temp_db, owner="alice")

    schema = _introspect_schema(db_path=temp_db, allowed_tables=None)
    assert "alice_notes" in {t["table"] for t in schema}


def test_sql_validator_rejects_query_against_others_private_table(temp_db):
    _make_private_table(temp_db, owner="alice")

    state = {
        "final_sql": "SELECT * FROM alice_notes",
        "sql_attempts": [],
        "username": "bob",
        "is_superuser": False,
    }
    result = sql_validator(state)
    attempt = result["sql_attempts"][-1]
    assert attempt["valid"] is False
    assert "unknown/disallowed table" in attempt["error"]


def test_sql_validator_allows_owner_to_query_own_private_table(temp_db):
    _make_private_table(temp_db, owner="alice")

    state = {
        "final_sql": "SELECT * FROM alice_notes",
        "sql_attempts": [],
        "username": "alice",
        "is_superuser": False,
    }
    result = sql_validator(state)
    assert result["sql_attempts"][-1]["valid"] is True


def test_sql_validator_allows_superuser_to_query_any_table(temp_db):
    _make_private_table(temp_db, owner="alice")

    state = {
        "final_sql": "SELECT * FROM alice_notes",
        "sql_attempts": [],
        "username": "admin",
        "is_superuser": True,
    }
    result = sql_validator(state)
    assert result["sql_attempts"][-1]["valid"] is True
