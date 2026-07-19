"""LangGraph wiring for the multi-agent NL-to-SQL system (spec section 4).

    input_guard -> schema_retriever -> sql_generator -> sql_validator
        --valid--------------------------------------> sql_executor -> response_formatter -> END
        --invalid (attempt 1)--> sql_regenerator -> sql_validator (loop)
        --invalid (attempt 2)--> enqueue_review -> [interrupt] -> await_decision
                                                                     --approved--> sql_executor -> ...
                                                                     --rejected--> END (failed)
                                                                     --pending---> END (escalated, resumable)

Business retry (invalid SQL -> regenerate once -> human review) is a
semantic loop capped by MAX_REGENERATION_ATTEMPTS. Technical retry (a node
raising) is handled per-node by resilience.resilient_node and is
orthogonal to this graph structure -- see resilience.py.
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.human_review_agent import await_decision_node, enqueue_review_node
from agents.input_guard import invoke as input_guard_node
from agents.response_formatter import invoke as response_formatter_node
from agents.schema_retriever import invoke as schema_retriever_node
from agents.sql_executor import invoke as sql_executor_node
from agents.sql_generator import invoke as sql_generator_node
from agents.sql_regenerator import invoke as sql_regenerator_node
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
        return "sql_regenerator"
    return "enqueue_review"


def route_after_decision(state: AgentState) -> str:
    if state.get("status") == "reviewed":
        return "sql_executor"
    return END  # "escalated" (still pending, resumable later) or "failed" (rejected)


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("input_guard", input_guard_node)
    graph.add_node("schema_retriever", schema_retriever_node)
    graph.add_node("sql_generator", sql_generator_node)
    graph.add_node("sql_validator", sql_validator_node)
    graph.add_node("sql_regenerator", sql_regenerator_node)
    graph.add_node("enqueue_review", enqueue_review_node)
    graph.add_node("await_decision", await_decision_node)
    graph.add_node("sql_executor", sql_executor_node)
    graph.add_node("response_formatter", response_formatter_node)

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges(
        "input_guard", route_after_input_guard, ["schema_retriever", END]
    )
    graph.add_edge("schema_retriever", "sql_generator")
    graph.add_edge("sql_generator", "sql_validator")
    graph.add_conditional_edges(
        "sql_validator",
        route_after_validation,
        ["sql_executor", "sql_regenerator", "enqueue_review"],
    )
    graph.add_edge("sql_regenerator", "sql_validator")
    graph.add_edge("enqueue_review", "await_decision")
    graph.add_conditional_edges(
        "await_decision", route_after_decision, ["sql_executor", END]
    )
    graph.add_edge("sql_executor", "response_formatter")
    graph.add_edge("response_formatter", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["await_decision"])


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


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
    return get_graph().invoke(initial_state, config)


def resume_review(trace_id: str) -> AgentState:
    """Resumes a run that's paused awaiting a human review decision."""
    config = {"configurable": {"thread_id": trace_id}}
    return get_graph().invoke(None, config)
