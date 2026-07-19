"""Human Review Agent (spec section 3.6, contract in section 10).

Split into two graph nodes so the checkpointer can interrupt at exactly
the right point:

- enqueue_review: runs automatically once regeneration has also failed.
  Writes the masked query + both failed SQL attempts + schema context to
  the review_queue table and marks the state as escalated.
- await_decision: the node the graph is compiled with
  `interrupt_before=["await_decision"]`, so the graph pauses right after
  enqueue_review returns. Resuming (graph.invoke(None, config)) re-enters
  here, which re-reads the queue row fresh -- if a reviewer has since
  called human_review.queue.decide(...), the corrected/approved SQL flows
  back into state.final_sql and the graph continues to the Executor; if
  rejected, the flow ends with a failed status; if still pending, it ends
  as escalated again and can be resumed later the same way.
"""
from human_review import queue
from middleware.tracing import traced_node
from resilience import resilient_node


def enqueue_review(state: dict) -> dict:
    review_id = queue.enqueue(
        trace_id=state["trace_id"],
        user_query_masked=state.get("user_query_masked", ""),
        sql_attempts=state.get("sql_attempts", []),
        schema_context=state.get("schema_context", []),
    )
    state["human_review"] = {"required": True, "reviewer": None, "decision": None, "review_id": review_id}
    state["status"] = "escalated"
    return state


def await_decision(state: dict) -> dict:
    review = state["human_review"]
    row = queue.get(review["review_id"])

    if row is None or row["status"] == "pending":
        state["status"] = "escalated"
        return state

    if row["status"] == "approved":
        state["final_sql"] = row["decision_sql"]
        state["human_review"]["decision"] = "approved"
        state["human_review"]["reviewer"] = row["reviewer"]
        state["status"] = "reviewed"
    else:
        state["human_review"]["decision"] = "rejected"
        state["human_review"]["reviewer"] = row["reviewer"]
        state["status"] = "failed"
        state["final_answer"] = (
            "I couldn't safely answer that -- a reviewer has been notified."
        )

    return state


enqueue_review_node = resilient_node()(traced_node(enqueue_review))
await_decision_node = resilient_node()(traced_node(await_decision))
