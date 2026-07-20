"""Shared LangGraph state shape (spec section 4)."""
from typing import Any, TypedDict


class SQLAttempt(TypedDict, total=False):
    attempt: int
    sql: str
    valid: bool
    error: str | None


class HumanReview(TypedDict, total=False):
    required: bool
    reviewer: str | None
    decision: str | None  # "approved" | "rejected" | None
    review_id: int | None


class AgentState(TypedDict, total=False):
    trace_id: str
    username: str
    is_superuser: bool
    user_query_raw: str
    user_query_masked: str
    schema_context: list[dict[str, Any]]
    sql_attempts: list[SQLAttempt]
    final_sql: str | None
    execution_result: list[dict[str, Any]] | None
    final_answer: str | None
    status: str  # "success" | "escalated" | "failed" | "error"
    human_review: HumanReview
    error_detail: dict[str, Any] | None
    _force_provider: str | None
    _generation_mode: str | None  # "initial" | "regenerate", set by agents/sql_generator.py
    critic_feedback: str | None  # set by agents/critic.py when AGENTIC_CRITIC_ENABLED and the answer looks insufficient
