"""SQL Validator Agent (spec section 3.4).

Checks: syntax validity (sqlglot), table/column existence against the live
schema, and guardrail compliance (statement-type allow-list, deny-listed
keywords, no stacked statements). Appends the outcome to state.sql_attempts
so the Orchestrator's routing logic (see graph.py) can decide the next hop.
"""
import auth
from agents.schema_retriever import _introspect_schema
from middleware.guardrails import apply_row_limit, check_sql
from middleware.tracing import traced_node
from resilience import resilient_node


def sql_validator(state: dict) -> dict:
    sql = state.get("final_sql", "")

    username = state.get("username")
    visible = (
        None
        if username is None
        else auth.visible_tables(username, state.get("is_superuser", False))
    )
    full_schema = _introspect_schema(allowed_tables=visible)
    allowed_tables = {t["table"] for t in full_schema}
    allowed_columns = {c["name"] for t in full_schema for c in t["columns"]}

    valid, error = check_sql(sql, allowed_tables=allowed_tables, allowed_columns=allowed_columns)
    if valid:
        sql = apply_row_limit(sql)
        state["final_sql"] = sql

    state.setdefault("sql_attempts", [])
    state["sql_attempts"].append(
        {
            "attempt": len(state["sql_attempts"]) + 1,
            "sql": sql,
            "valid": valid,
            "error": error,
        }
    )
    return state


invoke = resilient_node()(traced_node(sql_validator))
