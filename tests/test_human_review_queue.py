from human_review import queue


def test_enqueue_and_list_pending(temp_db):
    review_id = queue.enqueue(
        trace_id="trace-1",
        user_query_masked="how many orders?",
        sql_attempts=[
            {"attempt": 1, "sql": "SELECT * FROM bogus", "valid": False, "error": "no such table"},
            {"attempt": 2, "sql": "SELECT 1;DROP TABLE x", "valid": False, "error": "stacked statements"},
        ],
        schema_context=[{"table": "orders", "columns": []}],
        db_path=temp_db,
    )

    pending = queue.list_pending(db_path=temp_db)
    assert len(pending) == 1
    assert pending[0]["review_id"] == review_id
    attempts = queue.get_sql_attempts(pending[0])
    assert len(attempts) == 2
    assert attempts[1]["sql"] == "SELECT 1;DROP TABLE x"
    assert queue.latest_failed_sql(pending[0]) == "SELECT 1;DROP TABLE x"


def test_enqueue_preserves_more_than_two_attempts(temp_db):
    # config/guardrail_policy.yaml currently allows 2 regenerations (3 total
    # attempts) before escalation -- sql_attempts must be stored as a full
    # list, not two fixed columns, or the last (most relevant) attempt gets
    # silently dropped.
    attempts = [
        {"attempt": 1, "sql": "SELECT * FROM a", "valid": False, "error": "e1"},
        {"attempt": 2, "sql": "SELECT * FROM b", "valid": False, "error": "e2"},
        {"attempt": 3, "sql": "SELECT * FROM c", "valid": False, "error": "e3"},
    ]
    review_id = queue.enqueue(
        trace_id="trace-many",
        user_query_masked="q",
        sql_attempts=attempts,
        schema_context=[],
        db_path=temp_db,
    )

    row = queue.get(review_id, db_path=temp_db)
    stored = queue.get_sql_attempts(row)
    assert stored == attempts
    assert queue.latest_failed_sql(row) == "SELECT * FROM c"


def test_decide_approve_updates_status(temp_db):
    review_id = queue.enqueue(
        trace_id="trace-2",
        user_query_masked="q",
        sql_attempts=[{"attempt": 1, "sql": "SELECT 1", "valid": False, "error": "e"}],
        schema_context=[],
        db_path=temp_db,
    )

    queue.decide(review_id, approved=True, reviewer="alice", decision_sql="SELECT 1", db_path=temp_db)

    row = queue.get(review_id, db_path=temp_db)
    assert row["status"] == "approved"
    assert row["reviewer"] == "alice"
    assert row["decision_sql"] == "SELECT 1"
    assert queue.list_pending(db_path=temp_db) == []


def test_decide_reject_updates_status(temp_db):
    review_id = queue.enqueue(
        trace_id="trace-3",
        user_query_masked="q",
        sql_attempts=[{"attempt": 1, "sql": "SELECT 1", "valid": False, "error": "e"}],
        schema_context=[],
        db_path=temp_db,
    )

    queue.decide(review_id, approved=False, reviewer="bob", decision_reason="unsafe", db_path=temp_db)

    row = queue.get(review_id, db_path=temp_db)
    assert row["status"] == "rejected"
    assert row["decision_reason"] == "unsafe"
