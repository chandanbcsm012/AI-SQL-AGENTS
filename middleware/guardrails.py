"""Guardrail middleware (spec section 6): input, SQL, and output checks.

Every check returns a (passed: bool, reason: str | None) tuple so callers
can log the specific rule that fired rather than a generic rejection.
"""
import re
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

from middleware.pii import contains_pii

_POLICY_PATH = Path(__file__).parent.parent / "config" / "guardrail_policy.yaml"


def load_policy() -> dict:
    if _POLICY_PATH.exists():
        return yaml.safe_load(_POLICY_PATH.read_text()) or {}
    return {}


class GuardrailViolation(Exception):
    """Raised by callers that want a hard stop rather than a bool check."""


def check_input(query: str, policy: dict | None = None) -> tuple[bool, str | None]:
    policy = policy or load_policy()
    cfg = policy.get("input", {})

    max_len = cfg.get("max_query_length", 1000)
    if len(query) > max_len:
        return False, f"query exceeds max length of {max_len} chars"

    lowered = query.lower()
    for phrase in cfg.get("prompt_injection_phrases", []):
        if phrase.lower() in lowered:
            return False, f"prompt-injection phrase detected: '{phrase}'"

    return True, None


def check_sql(
    sql: str,
    allowed_tables: set[str] | None = None,
    allowed_columns: set[str] | None = None,
    policy: dict | None = None,
) -> tuple[bool, str | None]:
    policy = policy or load_policy()
    cfg = policy.get("sql", {})

    if cfg.get("deny_multiple_statements", True):
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
        if len(statements) > 1:
            return False, "multiple SQL statements are not allowed"

    for token in cfg.get("deny_comment_tokens", []):
        if token in sql:
            return False, f"SQL comments are not allowed ('{token}')"

    upper_sql = sql.upper()
    for keyword in cfg.get("denylist_keywords", []):
        if re.search(rf"\b{re.escape(keyword)}\b", upper_sql):
            return False, f"disallowed keyword: {keyword}"

    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception as e:
        return False, f"SQL failed to parse: {e}"

    allowed_types = set(cfg.get("allowed_statement_types", ["SELECT"]))
    if not isinstance(parsed, exp.Select) and "SELECT" in allowed_types:
        return False, "only SELECT statements are allowed"

    if allowed_tables is not None:
        referenced_tables = {t.name.lower() for t in parsed.find_all(exp.Table)}
        unknown = referenced_tables - {t.lower() for t in allowed_tables}
        if unknown:
            return False, f"unknown/disallowed table(s): {', '.join(sorted(unknown))}"

    if allowed_columns is not None:
        referenced_columns = {c.name.lower() for c in parsed.find_all(exp.Column)}
        unknown = referenced_columns - {c.lower() for c in allowed_columns} - {"*"}
        if unknown:
            return False, f"unknown/disallowed column(s): {', '.join(sorted(unknown))}"

    return True, None


def apply_row_limit(sql: str, policy: dict | None = None) -> str:
    """Auto-appends LIMIT if absent; caps it if present but too high."""
    policy = policy or load_policy()
    cfg = policy.get("sql", {})
    default_limit = cfg.get("default_row_limit", 500)
    max_limit = cfg.get("max_row_limit", 500)

    parsed = sqlglot.parse_one(sql, read="sqlite")
    existing_limit = parsed.args.get("limit")
    if existing_limit is None:
        parsed = parsed.limit(default_limit)
    else:
        try:
            value = int(existing_limit.expression.this)
            if value > max_limit:
                parsed = parsed.limit(max_limit)
        except (AttributeError, ValueError):
            pass

    return parsed.sql(dialect="sqlite")


def check_output(text: str, policy: dict | None = None) -> tuple[bool, str | None]:
    policy = policy or load_policy()
    cfg = policy.get("output", {})

    if cfg.get("scan_for_pii", True) and contains_pii(text):
        return False, "unmasked PII detected in output"

    if cfg.get("block_stack_traces", True) and re.search(
        r"Traceback \(most recent call last\)", text
    ):
        return False, "stack trace leaked into output"

    if cfg.get("block_db_file_paths", True) and re.search(r"[./\w-]+\.db\b", text):
        return False, "database file path leaked into output"

    return True, None
