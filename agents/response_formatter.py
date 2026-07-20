"""Response Formatter Agent (spec section 3.8, prompt template in section 9).

Execution results come straight from the live DB and may contain raw PII,
so rows are masked before ever being sent to an LLM (especially relevant
for the cloud Gemini provider in prod). The formatted answer is then
rehydrated for the original requesting user (config: rehydrate_in_response)
before the output guardrail does a final leak scan.

Deliberately NOT streamed straight from the LLM to the browser: the output
guardrail (check_output) must see the *complete* answer before anything is
shown, or a leaked/unsafe response could already be on screen by the time
the check runs. streamlit_app.py instead replays the finished, guardrail-
approved answer via st.write_stream for the same perceived-latency benefit
without that gap.
"""
import json

from langchain_core.messages import HumanMessage

from middleware.cache import get as cache_get
from middleware.cache import make_key
from middleware.cache import set as cache_set
from middleware.guardrails import check_output, load_policy as load_guardrail_policy
from middleware.pii import load_policy as load_pii_policy
from middleware.pii import mask_text, unmask_text
from middleware.tracing import traced_node
from model_factory import ModelRole, get_chat_model, resolve_model_name
from resilience import resilient_node

PROMPT = """Given the user's question and the query result rows below, write a concise answer
using Markdown formatting where it improves readability:
- Use a Markdown table when there are multiple rows and more than one column.
- Use a bullet or numbered list for a short enumeration of items.
- Use **bold** to highlight the specific number/value that answers the question.
- Use inline code (backticks) for identifiers, column names, or exact values.
Do not invent data not present in the rows. If rows are empty, say no matching data was found.

Question: {user_query_masked}
Rows: {execution_result}

Answer:
"""


def response_formatter(state: dict) -> dict:
    trace_id = state["trace_id"]
    rows = state.get("execution_result") or []
    masked_rows_json = mask_text(json.dumps(rows, default=str), trace_id, policy=load_pii_policy())

    provider = state.get("_force_provider")
    prompt = PROMPT.format(
        user_query_masked=state.get("user_query_masked", ""),
        execution_result=masked_rows_json,
    )

    model_name = resolve_model_name(ModelRole.GENERAL, provider)
    cache_key = make_key("response_formatter", model_name, prompt)
    cached = cache_get(cache_key)
    if cached is not None:
        answer_masked = cached
    else:
        llm = get_chat_model(role=ModelRole.GENERAL, provider=provider)
        response = llm.invoke([HumanMessage(content=prompt)])
        answer_masked = response.content.strip()
        cache_set(cache_key, answer_masked)

    # Guardrail runs on the still-masked answer: rows were pre-masked before
    # reaching the LLM, so any raw PII pattern here means it leaked through
    # (hallucination or a masking gap), not an intentional rehydration.
    guardrail_policy = load_guardrail_policy()
    ok, reason = check_output(answer_masked, policy=guardrail_policy)
    if not ok:
        # No human-review row is enqueued for this path (unlike the SQL
        # validation escalation in agents/human_review_agent.py) -- say so
        # plainly rather than implying an escalation that hasn't happened.
        answer = (
            "I found a potential data leak while preparing that answer, so I'm not "
            "showing it. Try rephrasing your question, or ask an admin to check "
            "logs/app.log for this trace_id."
        )
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
