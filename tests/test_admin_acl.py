import io

import pytest

import auth
from db import admin


def test_create_table_records_owner(temp_db):
    admin.execute_write(
        "CREATE TABLE alice_notes (id INTEGER PRIMARY KEY, note TEXT)",
        username="alice",
        is_superuser=False,
        db_path=temp_db,
    )
    assert auth.get_table_owner("alice_notes", db_path=temp_db) == "alice"


def test_owner_can_query_own_table(temp_db):
    admin.execute_write(
        "CREATE TABLE alice_notes (id INTEGER PRIMARY KEY, note TEXT)",
        username="alice",
        is_superuser=False,
        db_path=temp_db,
    )
    columns, rows = admin.execute_write(
        "SELECT * FROM alice_notes", username="alice", is_superuser=False, db_path=temp_db
    )
    assert rows == []
    assert columns == ["id", "note"]


def test_other_user_cannot_query_private_table(temp_db):
    admin.execute_write(
        "CREATE TABLE alice_notes (id INTEGER PRIMARY KEY, note TEXT)",
        username="alice",
        is_superuser=False,
        db_path=temp_db,
    )
    with pytest.raises(admin.AccessDeniedError):
        admin.execute_write(
            "SELECT * FROM alice_notes", username="bob", is_superuser=False, db_path=temp_db
        )


def test_superuser_can_query_any_private_table(temp_db):
    admin.execute_write(
        "CREATE TABLE alice_notes (id INTEGER PRIMARY KEY, note TEXT)",
        username="alice",
        is_superuser=False,
        db_path=temp_db,
    )
    columns, _ = admin.execute_write(
        "SELECT * FROM alice_notes", username="admin", is_superuser=True, db_path=temp_db
    )
    assert columns == ["id", "note"]


def test_execute_script_records_owner_and_enforces_access(temp_db):
    admin.execute_script(
        "CREATE TABLE bob_data (id INTEGER PRIMARY KEY);",
        username="bob",
        is_superuser=False,
        db_path=temp_db,
    )
    assert auth.get_table_owner("bob_data", db_path=temp_db) == "bob"

    with pytest.raises(admin.AccessDeniedError):
        admin.execute_script(
            "SELECT * FROM bob_data;", username="alice", is_superuser=False, db_path=temp_db
        )


def test_import_csv_records_owner_and_blocks_other_users(temp_db):
    csv_bytes = b"id,note\n1,hello\n"
    n = admin.import_csv(
        io.BytesIO(csv_bytes), "carol_table", username="carol", is_superuser=False, db_path=temp_db
    )
    assert n == 1
    assert auth.get_table_owner("carol_table", db_path=temp_db) == "carol"

    with pytest.raises(admin.AccessDeniedError):
        admin.import_csv(
            io.BytesIO(csv_bytes), "carol_table", username="dave", is_superuser=False, db_path=temp_db
        )


def test_import_csv_into_public_seed_table_allowed_for_anyone(temp_db):
    csv_bytes = (
        b"full_name,email,phone,city,country,signup_date\n"
        b"New User,new.user@example.com,+1-555-0100,Nowhere,USA,2026-01-01\n"
    )
    n = admin.import_csv(
        io.BytesIO(csv_bytes), "customer", username="alice", is_superuser=False, db_path=temp_db
    )
    assert n == 1
