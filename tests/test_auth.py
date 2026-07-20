import sqlite3

import auth


def test_default_users_are_seeded(temp_db):
    admin = auth.authenticate("admin", "admin123", db_path=temp_db)
    alice = auth.authenticate("alice", "alice123", db_path=temp_db)
    bob = auth.authenticate("bob", "bob123", db_path=temp_db)
    carol = auth.authenticate("carol", "carol123", db_path=temp_db)

    assert admin == {"username": "admin", "role": "admin", "is_superuser": True}
    assert alice == {"username": "alice", "role": "editor", "is_superuser": False}
    assert bob == {"username": "bob", "role": "editor", "is_superuser": False}
    assert carol == {"username": "carol", "role": "viewer", "is_superuser": False}


def test_wrong_password_rejected(temp_db):
    assert auth.authenticate("alice", "wrong-password", db_path=temp_db) is None


def test_unknown_user_rejected(temp_db):
    assert auth.authenticate("nobody", "whatever", db_path=temp_db) is None


def test_create_user_and_authenticate(temp_db):
    auth.create_user("dave", "dave-secret", db_path=temp_db)
    assert auth.authenticate("dave", "dave-secret", db_path=temp_db) == {
        "username": "dave",
        "role": "editor",
        "is_superuser": False,
    }


def test_create_user_rejects_unknown_role(temp_db):
    import pytest

    with pytest.raises(ValueError):
        auth.create_user("eve", "eve-secret", role="root", db_path=temp_db)


def test_can_write_by_role():
    assert auth.can_write("editor")
    assert auth.can_write("admin")
    assert not auth.can_write("viewer")


def test_seed_tables_are_public_to_everyone(temp_db):
    alice_view = auth.visible_tables("alice", is_superuser=False, db_path=temp_db)
    bob_view = auth.visible_tables("bob", is_superuser=False, db_path=temp_db)

    for table in ("customer", "product", "orders", "order_item"):
        assert table in alice_view
        assert table in bob_view


def test_superuser_has_no_restriction(temp_db):
    assert auth.visible_tables("admin", is_superuser=True, db_path=temp_db) is None


def test_private_table_visible_only_to_owner_and_superuser(temp_db):
    conn = sqlite3.connect(temp_db)
    conn.execute("CREATE TABLE alice_private (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    auth.record_table_owner("alice_private", "alice", db_path=temp_db)

    alice_view = auth.visible_tables("alice", is_superuser=False, db_path=temp_db)
    bob_view = auth.visible_tables("bob", is_superuser=False, db_path=temp_db)

    assert "alice_private" in alice_view
    assert "alice_private" not in bob_view
    assert auth.can_access_table("alice_private", "alice", is_superuser=False, db_path=temp_db)
    assert not auth.can_access_table("alice_private", "bob", is_superuser=False, db_path=temp_db)
    assert auth.can_access_table("alice_private", "bob", is_superuser=True, db_path=temp_db)


def test_system_tables_never_visible_to_regular_users(temp_db):
    alice_view = auth.visible_tables("alice", is_superuser=False, db_path=temp_db)
    assert "app_user" not in alice_view
    assert "table_ownership" not in alice_view
    assert not auth.can_access_table("app_user", "alice", is_superuser=False, db_path=temp_db)
