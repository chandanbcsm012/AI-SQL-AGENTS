import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from db.init_db import init_db


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
