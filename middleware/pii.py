"""PII masking middleware (spec section 5).

Detection is regex-based for structured PII (emails, phones, credit cards,
IPs, SSN/Aadhaar/PAN-style IDs). Masking is reversible tokenization
(`<<PII_EMAIL_1>>`) held in an in-memory map scoped to `trace_id` -- never
persisted to logs or traces. Only the Response Formatter may ask the vault
to rehydrate, and only for the original requesting user.
"""
import re
import threading
from pathlib import Path

import yaml

_POLICY_PATH = Path(__file__).parent.parent / "config" / "pii_policy.yaml"

# entity -> (regex, token_prefix)
_PATTERNS = {
    "EMAIL_ADDRESS": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "PHONE_NUMBER": re.compile(r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{4}"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "US_SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "IN_AADHAAR": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "IN_PAN": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


def load_policy() -> dict:
    if _POLICY_PATH.exists():
        return yaml.safe_load(_POLICY_PATH.read_text()) or {}
    return {}


class PIIVault:
    """Holds the token -> original-value map for one trace_id at a time.

    Scoped in-memory (swap for Redis in production, keyed by trace_id) and
    discarded once the request completes -- never written to disk or logs.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._store: dict[str, dict[str, str]] = {}

    def put(self, trace_id: str, token: str, original: str) -> None:
        with self._lock:
            self._store.setdefault(trace_id, {})[token] = original

    def get_map(self, trace_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._store.get(trace_id, {}))

    def clear(self, trace_id: str) -> None:
        with self._lock:
            self._store.pop(trace_id, None)


_vault = PIIVault()


def get_vault() -> PIIVault:
    return _vault


def mask_text(text: str, trace_id: str, policy: dict | None = None) -> str:
    """Replaces detected PII with reversible `<<PREFIX_n>>` tokens and
    records the mapping in the vault under trace_id."""
    if not text:
        return text
    policy = policy or load_policy()
    entities = policy.get("entities", {})
    masked = text
    counters: dict[str, int] = {}

    for entity, pattern in _PATTERNS.items():
        cfg = entities.get(entity)
        if not cfg or cfg.get("action") == "drop":
            if cfg and cfg.get("action") == "drop":
                masked = pattern.sub("", masked)
            continue
        prefix = cfg.get("token_prefix", entity)

        def _sub(match: re.Match, prefix=prefix, entity=entity) -> str:
            counters[entity] = counters.get(entity, 0) + 1
            token = f"<<{prefix}_{counters[entity]}>>"
            _vault.put(trace_id, token, match.group(0))
            return token

        masked = pattern.sub(_sub, masked)

    return masked


def unmask_text(text: str, trace_id: str) -> str:
    """Rehydrates tokens back to original values. Only ever call this for
    the final response shown to the original requesting user -- never for
    logs or traces."""
    if not text:
        return text
    token_map = _vault.get_map(trace_id)
    result = text
    for token, original in token_map.items():
        result = result.replace(token, original)
    return result


def contains_pii(text: str, policy: dict | None = None) -> bool:
    """Defense-in-depth scan used by output guardrails to catch any PII
    that leaked through unmasked (e.g. echoed from raw DB rows)."""
    if not text:
        return False
    policy = policy or load_policy()
    entities = policy.get("entities", {})
    for entity, pattern in _PATTERNS.items():
        if entity in entities and pattern.search(text):
            return True
    return False
