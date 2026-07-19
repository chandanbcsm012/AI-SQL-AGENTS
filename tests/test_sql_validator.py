from agents.sql_validator import sql_validator


def test_valid_select_is_marked_valid(temp_db):
    state = {"final_sql": "SELECT * FROM customer", "sql_attempts": []}
    result = sql_validator(state)

    attempt = result["sql_attempts"][-1]
    assert attempt["valid"] is True
    assert attempt["error"] is None
    assert "LIMIT" in result["final_sql"].upper()


def test_unknown_table_is_marked_invalid(temp_db):
    state = {"final_sql": "SELECT * FROM nonexistent_table", "sql_attempts": []}
    result = sql_validator(state)

    attempt = result["sql_attempts"][-1]
    assert attempt["valid"] is False
    assert "unknown/disallowed table" in attempt["error"]


def test_disallowed_statement_is_marked_invalid(temp_db):
    state = {"final_sql": "DELETE FROM customer", "sql_attempts": []}
    result = sql_validator(state)

    attempt = result["sql_attempts"][-1]
    assert attempt["valid"] is False
