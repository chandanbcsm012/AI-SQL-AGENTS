"""End-to-end scenarios (spec section 8, deliverable 7):
  (a) valid on first try
  (b) valid after regeneration
  (c) escalates to human review once the regeneration cap is exhausted

The LLM is stubbed via FakeLLM so these are deterministic and don't
require a running Ollama/Gemini endpoint.
"""
import uuid

import agents.response_formatter as response_formatter
import agents.sql_generator as sql_generator
from graph import MAX_REGENERATION_ATTEMPTS, get_graph
from human_review import queue
from tests.conftest import FakeLLM


def _run(temp_db, monkeypatch, sql_attempts: list[str], formatter_answer: str):
    # sql_generator is one node reused across attempts (initial + every
    # regeneration), so its get_chat_model must return the SAME FakeLLM
    # instance every call -- a fresh instance per call would just replay
    # sql_attempts[0] forever instead of advancing through the list.
    shared_llm = FakeLLM(list(sql_attempts))
    monkeypatch.setattr(sql_generator, "get_chat_model", lambda **kw: shared_llm)
    monkeypatch.setattr(response_formatter, "get_chat_model", lambda **kw: FakeLLM([formatter_answer]))

    trace_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": trace_id}}
    initial_state = {"user_query_raw": "how many customers are there?", "trace_id": trace_id}
    return trace_id, get_graph().invoke(initial_state, config)


def test_valid_on_first_try(temp_db, monkeypatch):
    _, result = _run(
        temp_db,
        monkeypatch,
        sql_attempts=["SELECT COUNT(*) AS n FROM customer"],
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
        sql_attempts=["SELECT * FROM ghost_table", "SELECT COUNT(*) AS n FROM customer"],
        formatter_answer="There are 6 customers.",
    )

    assert result["status"] == "success"
    assert len(result["sql_attempts"]) == 2
    assert result["sql_attempts"][0]["valid"] is False
    assert result["sql_attempts"][1]["valid"] is True
    assert result["final_answer"] == "There are 6 customers."
    # the second (regeneration) attempt should carry that mode in its trace
    assert result["_generation_mode"] == "regenerate"


def test_escalates_to_human_review_after_regeneration_cap_exhausted(temp_db, monkeypatch):
    # one initial attempt + MAX_REGENERATION_ATTEMPTS regenerations, all invalid
    invalid_sqls = [f"SELECT * FROM ghost_table_{i}" for i in range(MAX_REGENERATION_ATTEMPTS + 1)]
    trace_id, result = _run(
        temp_db,
        monkeypatch,
        sql_attempts=invalid_sqls,
        formatter_answer="unused",
    )

    assert result["status"] == "escalated"
    assert len(result["sql_attempts"]) == MAX_REGENERATION_ATTEMPTS + 1
    assert all(a["valid"] is False for a in result["sql_attempts"])
    assert result["human_review"]["required"] is True

    pending = queue.list_pending(db_path=temp_db)
    assert len(pending) == 1
    assert pending[0]["trace_id"] == trace_id
