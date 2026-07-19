"""End-to-end scenarios (spec section 8, deliverable 7):
  (a) valid on first try
  (b) valid after one regeneration
  (c) escalates to human review after a second invalid attempt

The LLM is stubbed via FakeLLM so these are deterministic and don't
require a running Ollama/Gemini endpoint.
"""
import uuid

import agents.response_formatter as response_formatter
import agents.sql_generator as sql_generator
import agents.sql_regenerator as sql_regenerator
from graph import get_graph
from human_review import queue
from tests.conftest import FakeLLM


def _run(temp_db, monkeypatch, generator_sql, formatter_answer, regenerator_sql=None):
    monkeypatch.setattr(sql_generator, "get_chat_model", lambda **kw: FakeLLM([generator_sql]))
    monkeypatch.setattr(response_formatter, "get_chat_model", lambda **kw: FakeLLM([formatter_answer]))
    if regenerator_sql is not None:
        monkeypatch.setattr(sql_regenerator, "get_chat_model", lambda **kw: FakeLLM([regenerator_sql]))

    trace_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": trace_id}}
    initial_state = {"user_query_raw": "how many customers are there?", "trace_id": trace_id}
    return trace_id, get_graph().invoke(initial_state, config)


def test_valid_on_first_try(temp_db, monkeypatch):
    _, result = _run(
        temp_db,
        monkeypatch,
        generator_sql="SELECT COUNT(*) AS n FROM customer",
        formatter_answer="There are 6 customers.",
    )

    assert result["status"] == "success"
    assert len(result["sql_attempts"]) == 1
    assert result["sql_attempts"][0]["valid"] is True
    assert result["final_answer"] == "There are 6 customers."
    assert result["execution_result"] is not None


def test_valid_after_regeneration(temp_db, monkeypatch):
    _, result = _run(
        temp_db,
        monkeypatch,
        generator_sql="SELECT * FROM ghost_table",
        regenerator_sql="SELECT COUNT(*) AS n FROM customer",
        formatter_answer="There are 6 customers.",
    )

    assert result["status"] == "success"
    assert len(result["sql_attempts"]) == 2
    assert result["sql_attempts"][0]["valid"] is False
    assert result["sql_attempts"][1]["valid"] is True
    assert result["final_answer"] == "There are 6 customers."


def test_escalates_to_human_review_after_second_failure(temp_db, monkeypatch):
    trace_id, result = _run(
        temp_db,
        monkeypatch,
        generator_sql="SELECT * FROM ghost_table",
        regenerator_sql="SELECT * FROM another_ghost_table",
        formatter_answer="unused",
    )

    assert result["status"] == "escalated"
    assert len(result["sql_attempts"]) == 2
    assert all(a["valid"] is False for a in result["sql_attempts"])
    assert result["human_review"]["required"] is True

    pending = queue.list_pending(db_path=temp_db)
    assert len(pending) == 1
    assert pending[0]["trace_id"] == trace_id
