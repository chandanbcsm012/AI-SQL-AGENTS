"""NL->SQL Generator Agent.

A single node handles both the first-pass generation and every subsequent
regeneration: it inspects state.sql_attempts and, if the last attempt
failed validation, switches to a regeneration prompt that feeds the failed
SQL + validator error back in. There's no separate "regenerator" agent --
one node, two prompt modes, looping back through sql_validator until it
either passes or the business-retry cap (config/guardrail_policy.yaml:
escalation.max_regeneration_attempts) escalates to human review.

Output is a structured `SQLGeneration` object (Pydantic, parsed from the
model's JSON response) instead of regex-scraping SQL out of free text --
`_extract_sql`'s regex is now only a fallback for models/tests that don't
follow the JSON format instructions.

state["_generation_mode"] is set to "initial" or "regenerate" purely so
middleware/tracing.py can log which mode produced each attempt, since a
single node name no longer distinguishes that on its own.
"""
import re

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from middleware.cache import get as cache_get
from middleware.cache import make_key
from middleware.cache import set as cache_set
from middleware.tracing import traced_node
from model_factory import ModelRole, get_chat_model, resolve_model_name
from resilience import resilient_node


class SQLGeneration(BaseModel):
    sql: str = Field(description="A single valid SQLite SELECT statement. No markdown fences, no trailing semicolon.")
    reasoning: str = Field(default="", description="One sentence on how this SQL answers the question.")


_parser = PydanticOutputParser(pydantic_object=SQLGeneration)

GENERATE_PROMPT = """You are a SQL generation agent. Given the database schema below and the user's question,
produce ONE valid SQLite SELECT statement. Only use the tables/columns listed.

Schema:
{schema_context}

Question: {user_query_masked}

{format_instructions}
"""

REGENERATE_PROMPT = """The following SQL failed validation.

SQL: {previous_sql}
Error: {validator_error}
Schema: {schema_context}
Original question: {user_query_masked}

Produce a corrected SQLite SELECT statement only.

{format_instructions}
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


def _parse_response(raw: str) -> SQLGeneration:
    """Tries the structured JSON parse first; falls back to regex
    extraction for models (or FakeLLM in tests) that just return plain SQL
    text instead of following the JSON format instructions."""
    try:
        parsed = _parser.parse(raw)
        return SQLGeneration(sql=_extract_sql(parsed.sql), reasoning=parsed.reasoning)
    except Exception:
        return SQLGeneration(sql=_extract_sql(raw))


def sql_generator(state: dict) -> dict:
    attempts = state.get("sql_attempts", [])
    last_attempt = attempts[-1] if attempts else None
    is_regeneration = last_attempt is not None and not last_attempt["valid"]

    schema_text = _format_schema(state.get("schema_context", []))
    format_instructions = _parser.get_format_instructions()
    if is_regeneration:
        prompt = REGENERATE_PROMPT.format(
            previous_sql=last_attempt["sql"],
            validator_error=last_attempt["error"],
            schema_context=schema_text,
            user_query_masked=state.get("user_query_masked", ""),
            format_instructions=format_instructions,
        )
    else:
        prompt = GENERATE_PROMPT.format(
            schema_context=schema_text,
            user_query_masked=state.get("user_query_masked", ""),
            format_instructions=format_instructions,
        )

    provider = state.get("_force_provider")
    model_name = resolve_model_name(ModelRole.SQL_GEN, provider)
    cache_key = make_key("sql_generator", model_name, prompt)

    cached = cache_get(cache_key)
    if cached is not None:
        raw_content = cached
    else:
        llm = get_chat_model(role=ModelRole.SQL_GEN, provider=provider)
        response = llm.invoke([HumanMessage(content=prompt)])
        raw_content = response.content
        cache_set(cache_key, raw_content)

    generation = _parse_response(raw_content)

    state.setdefault("sql_attempts", [])
    state["final_sql"] = generation.sql
    state["_generation_mode"] = "regenerate" if is_regeneration else "initial"
    return state


invoke = resilient_node()(traced_node(sql_generator))
