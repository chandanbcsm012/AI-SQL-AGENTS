"""Response Formatter Agent (spec section 3.8, prompt template in section 9).

Execution results come straight from the live DB and may contain raw PII,
so rows are masked before ever being sent to an LLM (especially relevant
for the cloud Gemini provider in prod). The formatted answer is then
rehydrated for the original requesting user (config: rehydrate_in_response)
before the output guardrail does a final leak scan.
"""
import json

from langchain_core.messages import HumanMessage

from middleware.guardrails import check_output, load_policy as load_guardrail_policy
from middleware.pii import load_policy as load_pii_policy
from middleware.pii import mask_text, unmask_text
from middleware.tracing import traced_node
from model_factory import ModelRole, get_chat_model
from resilience import resilient_node

PROMPT = """Given the user's question and the query result rows below, write a concise natural-language
answer. Do not invent data not present in the rows. If rows are empty, say no matching data
was found.

Question: {user_query_masked}
Rows: {execution_result}

Answer:
"""


def response_formatter(state: dict) -> dict:
    trace_id = state["trace_id"]
    rows = state.get("execution_result") or []
    masked_rows_json = mask_text(json.dumps(rows, default=str), trace_id, policy=load_pii_policy())

    provider = state.get("_force_provider")
    llm = get_chat_model(role=ModelRole.GENERAL, provider=provider)
    prompt = PROMPT.format(
        user_query_masked=state.get("user_query_masked", ""),
        execution_result=masked_rows_json,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    answer_masked = response.content.strip()

    # Guardrail runs on the still-masked answer: rows were pre-masked before
    # reaching the LLM, so any raw PII pattern here means it leaked through
    # (hallucination or a masking gap), not an intentional rehydration.
    guardrail_policy = load_guardrail_policy()
    ok, reason = check_output(answer_masked, policy=guardrail_policy)
    if not ok:
        answer = "I'm unable to safely return that answer. A reviewer has been notified."
        state["status"] = "failed"
        state["error_detail"] = {"node": "response_formatter", "error": reason}
    else:
        pii_policy = load_pii_policy()
        answer = (
            unmask_text(answer_masked, trace_id)
            if pii_policy.get("rehydrate_in_response", True)
            else answer_masked
        )
        state["status"] = state.get("status") or "success"

    state["final_answer"] = answer
    return state


invoke = resilient_node()(traced_node(response_formatter))
