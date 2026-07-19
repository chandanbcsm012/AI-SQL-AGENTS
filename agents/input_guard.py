"""Input PII-mask + guardrail step (spec section 4 diagram: "PII Mask +
Guardrail: Input"). Not part of the 8-agent roster -- this is the
cross-cutting middleware applied at the very start of the graph, kept as
its own node so it gets the same tracing/resilience wrapping as every
other step.
"""
import uuid

from middleware.guardrails import check_input
from middleware.pii import mask_text
from middleware.tracing import traced_node
from resilience import resilient_node


def input_guard(state: dict) -> dict:
    state.setdefault("trace_id", str(uuid.uuid4()))
    query = state["user_query_raw"]

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
