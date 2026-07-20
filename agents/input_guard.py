"""Input PII-mask + guardrail step (spec section 4 diagram: "PII Mask +
Guardrail: Input"). Not part of the 8-agent roster -- this is the
cross-cutting middleware applied at the very start of the graph, kept as
its own node so it gets the same tracing/resilience wrapping as every
other step.
"""
import uuid

from middleware.guardrails import check_input, load_policy
from middleware.pii import mask_text
from middleware.rate_limit import check_rate_limit
from middleware.tracing import traced_node
from resilience import resilient_node


def input_guard(state: dict) -> dict:
    state.setdefault("trace_id", str(uuid.uuid4()))
    query = state["user_query_raw"]

    policy = load_policy()
    limit = policy.get("input", {}).get("rate_limit_per_minute", 30)
    username = state.get("username") or "anonymous"
    allowed, count = check_rate_limit(f"query:{username}", limit)
    if not allowed:
        state["status"] = "failed"
        state["final_answer"] = (
            f"You've hit the rate limit ({limit} requests/minute). Please try again shortly."
        )
        state["error_detail"] = {"node": "input_guard", "error": f"rate limit exceeded ({count}/{limit})"}
        return state

    ok, reason = check_input(query)
    if not ok:
        state["status"] = "failed"
        state["final_answer"] = (
            "I can't process that request: it was blocked by an input guardrail."
        )
        state["error_detail"] = {"node": "input_guard", "error": reason}
        return state

    state["user_query_masked"] = mask_text(query, state["trace_id"])
    return state


invoke = resilient_node()(traced_node(input_guard))
