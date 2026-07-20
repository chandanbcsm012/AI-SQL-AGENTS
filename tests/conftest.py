import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from db.init_db import init_db


@pytest.fixture(autouse=True)
def _no_real_rate_limit_or_cache(monkeypatch):
    """Tests must be hermetic -- never depend on Valkey/Qdrant/Postgres
    actually running, even though they do in this dev environment. Rate
    limiting and LLM-response caching would otherwise silently reach out to
    Valkey during a test run -- and worse, since several tests reuse the
    exact same question text, a real cache would make FakeLLM's canned
    responses go unconsumed (a hit from one test masking what the next
    test's mock intended to return)."""
    import agents.input_guard as input_guard
    import agents.response_formatter as response_formatter
    import agents.sql_generator as sql_generator

    monkeypatch.setattr(input_guard, "check_rate_limit", lambda key, limit: (True, 0))
    monkeypatch.setattr(sql_generator, "cache_get", lambda key: None)
    monkeypatch.setattr(sql_generator, "cache_set", lambda key, value: None)
    monkeypatch.setattr(response_formatter, "cache_get", lambda key: None)
    monkeypatch.setattr(response_formatter, "cache_set", lambda key, value: None)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Creates an isolated app.db copy and points every module that hardcodes
    DB_PATH at it, so tests never touch the real dev database."""
    db_path = tmp_path / "app.db"
    init_db(db_path)

    import agents.schema_retriever as schema_retriever
    import agents.sql_executor as sql_executor
    import auth
    import human_review.queue as review_queue
    from db import admin as db_admin

    monkeypatch.setattr(schema_retriever, "DB_PATH", db_path)
    monkeypatch.setattr(sql_executor, "DB_PATH", db_path)
    monkeypatch.setattr(review_queue, "DB_PATH", db_path)
    monkeypatch.setattr(auth, "DB_PATH", db_path)
    monkeypatch.setattr(db_admin, "DB_PATH", db_path)
    return db_path


class FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """Stub chat model: returns canned .content strings in order, one per
    .invoke() call, so agent flow (generator -> validator -> regenerator ->
    ...) is fully deterministic in tests."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    def invoke(self, _messages):
        if not self._responses:
            raise AssertionError("FakeLLM ran out of canned responses")
        return FakeLLMResponse(self._responses.pop(0))
