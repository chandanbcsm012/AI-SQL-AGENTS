"""NL->SQL Generator Agent (spec section 3.3, prompt template in section 9)."""
import re

from langchain_core.messages import HumanMessage

from middleware.tracing import traced_node
from model_factory import ModelRole, get_chat_model
from resilience import resilient_node

PROMPT = """You are a SQL generation agent. Given the database schema below and the user's question,
produce ONE valid SQLite SELECT statement. Only use the tables/columns listed. Do not
explain, only output SQL.

Schema:
{schema_context}

Question: {user_query_masked}

SQL:
"""


def _format_schema(schema_context: list[dict]) -> str:
    lines = []
    for entry in schema_context:
        cols = ", ".join(c["name"] for c in entry["columns"])
        lines.append(f"- {entry['table']}({cols})")
    return "\n".join(lines)


def _extract_sql(raw: str) -> str:
    match = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    sql = match.group(1) if match else raw
    return sql.strip().rstrip(";")


def sql_generator(state: dict) -> dict:
    provider = state.get("_force_provider")
    llm = get_chat_model(role=ModelRole.SQL_GEN, provider=provider)

    prompt = PROMPT.format(
        schema_context=_format_schema(state.get("schema_context", [])),
        user_query_masked=state.get("user_query_masked", ""),
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    sql = _extract_sql(response.content)

    state.setdefault("sql_attempts", [])
    state["final_sql"] = sql
    return state


invoke = resilient_node()(traced_node(sql_generator))
