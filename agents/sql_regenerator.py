"""SQL Regenerator Agent (spec section 3.5, prompt template in section 9).

Invoked only on first validator failure -- same job as the Generator but
prompted with the previous SQL and the validator's error message.
"""
from langchain_core.messages import HumanMessage

from agents.sql_generator import _extract_sql, _format_schema
from middleware.tracing import traced_node
from model_factory import ModelRole, get_chat_model
from resilience import resilient_node

PROMPT = """The following SQL failed validation.

SQL: {previous_sql}
Error: {validator_error}
Schema: {schema_context}
Original question: {user_query_masked}

Produce a corrected SQLite SELECT statement only.

SQL:
"""


def sql_regenerator(state: dict) -> dict:
    provider = state.get("_force_provider")
    llm = get_chat_model(role=ModelRole.SQL_GEN, provider=provider)

    last_attempt = state["sql_attempts"][-1]
    prompt = PROMPT.format(
        previous_sql=last_attempt["sql"],
        validator_error=last_attempt["error"],
        schema_context=_format_schema(state.get("schema_context", [])),
        user_query_masked=state.get("user_query_masked", ""),
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    state["final_sql"] = _extract_sql(response.content)
    return state


invoke = resilient_node()(traced_node(sql_regenerator))
