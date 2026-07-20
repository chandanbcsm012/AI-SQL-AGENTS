"""LangGraph wiring for the multi-agent NL-to-SQL system (spec section 4).

    input_guard -> schema_retriever -> sql_generator -> sql_validator
        --valid--------------------------------------> sql_executor -> response_formatter -> END
        --invalid (attempt <= cap)--> sql_generator (self-loop, regeneration prompt) -> sql_validator
        --invalid (attempt > cap)---> enqueue_review -> [interrupt] -> await_decision
                                                                     --approved--> sql_executor -> ...
                                                                     --rejected--> END (failed)
                                                                     --pending---> END (escalated, resumable)

sql_generator is a single node for both the first pass and every
regeneration -- see agents/sql_generator.py's docstring for why there's no
separate "regenerator" agent.

Business retry (invalid SQL -> regenerate -> human review) is a semantic
loop capped by MAX_REGENERATION_ATTEMPTS. Technical retry (a node raising)
is handled per-node by resilience.resilient_node and is orthogonal to this
graph structure -- see resilience.py.
"""
import logging
import os

import sqlglot
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlglot import exp

import history
from agents.critic import invoke as critic_node
from agents.human_review_agent import await_decision_node, enqueue_review_node
from agents.input_guard import invoke as input_guard_node
from agents.response_formatter import invoke as response_formatter_node
from agents.schema_retriever import invoke as schema_retriever_node
from agents.sql_executor import invoke as sql_executor_node
from agents.sql_generator import invoke as sql_generator_node
from agents.sql_validator import invoke as sql_validator_node
from middleware.guardrails import load_policy
from state import AgentState

MAX_REGENERATION_ATTEMPTS = load_policy().get("escalation", {}).get("max_regeneration_attempts", 1)


def route_after_input_guard(state: AgentState) -> str:
    return END if state.get("status") == "failed" else "schema_retriever"


def route_after_validation(state: AgentState) -> str:
    last = state["sql_attempts"][-1]
    if last["valid"]:
        return "sql_executor"
    if len(state["sql_attempts"]) <= MAX_REGENERATION_ATTEMPTS:
        return "sql_generator"
    return "enqueue_review"


def route_after_decision(state: AgentState) -> str:
    if state.get("status") == "reviewed":
        return "sql_executor"
    return END  # "escalated" (still pending, resumable later) or "failed" (rejected)


logger = logging.getLogger("graph")


def _build_checkpointer():
    """Postgres-backed (persistent, survives a restart -- important for a
    paused human-review run) if DATABASE_URL/Postgres is reachable, else
    falls back to in-process MemorySaver. Opt out entirely via
    CHECKPOINTER_BACKEND=memory."""
    if os.getenv("CHECKPOINTER_BACKEND", "postgres") == "memory":
        return MemorySaver()

    try:
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver

        dsn = os.getenv("DATABASE_URL", "postgresql://sqlagents:sqlagents@localhost:5432/sqlagents")
        conn = psycopg.connect(dsn, autocommit=True, connect_timeout=2)
        checkpointer = PostgresSaver(conn)
        checkpointer.setup()
        return checkpointer
    except Exception as e:
        logger.warning("postgres_checkpointer_unavailable, falling back to MemorySaver: %s", e)
        return MemorySaver()


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("input_guard", input_guard_node)
    graph.add_node("schema_retriever", schema_retriever_node)
    graph.add_node("sql_generator", sql_generator_node)
    graph.add_node("sql_validator", sql_validator_node)
    graph.add_node("enqueue_review", enqueue_review_node)
    graph.add_node("await_decision", await_decision_node)
    graph.add_node("sql_executor", sql_executor_node)
    graph.add_node("response_formatter", response_formatter_node)
    graph.add_node("critic", critic_node)

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges(
        "input_guard", route_after_input_guard, ["schema_retriever", END]
    )
    graph.add_edge("schema_retriever", "sql_generator")
    graph.add_edge("sql_generator", "sql_validator")
    graph.add_conditional_edges(
        "sql_validator",
        route_after_validation,
        ["sql_executor", "sql_generator", "enqueue_review"],
    )
    graph.add_edge("enqueue_review", "await_decision")
    graph.add_conditional_edges(
        "await_decision", route_after_decision, ["sql_executor", END]
    )
    graph.add_edge("sql_executor", "response_formatter")
    graph.add_edge("response_formatter", "critic")
    graph.add_edge("critic", END)

    checkpointer = _build_checkpointer()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["await_decision"])


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def _referenced_tables(sql: str | None) -> list[str]:
    if not sql:
        return []
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
        return sorted({t.name for t in parsed.find_all(exp.Table)})
    except Exception:
        return []


def _log_activity(state: AgentState) -> None:
    """Best-effort activity-log write (query history + audit trail on
    Postgres, see history.py) -- never allowed to fail the actual request."""
    try:
        history.record_activity(
            trace_id=state.get("trace_id", ""),
            username=state.get("username"),
            question=state.get("user_query_raw", ""),
            sql=state.get("final_sql"),
            status=state.get("status", "unknown"),
            tables_touched=_referenced_tables(state.get("final_sql")),
        )
    except Exception:
        pass


def run_query(
    user_query: str,
    trace_id: str | None = None,
    username: str | None = None,
    is_superuser: bool = False,
) -> AgentState:
    """Starts a new run for a fresh user question.

    username/is_superuser scope which tables the Schema Retriever and SQL
    Validator will even consider -- a non-superuser never sees, and can
    never generate valid SQL against, another user's private tables. If
    username is omitted the run is unrestricted (no logged-in user, e.g.
    direct CLI/test use).
    """
    import uuid

    thread_id = trace_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    initial_state: AgentState = {
        "user_query_raw": user_query,
        "trace_id": thread_id,
        "username": username,
        "is_superuser": is_superuser,
    }
    result = get_graph().invoke(initial_state, config)
    _log_activity(result)
    return result


def resume_review(trace_id: str) -> AgentState:
    """Resumes a run that's paused awaiting a human review decision."""
    config = {"configurable": {"thread_id": trace_id}}
    result = get_graph().invoke(None, config)
    _log_activity(result)
    return result
