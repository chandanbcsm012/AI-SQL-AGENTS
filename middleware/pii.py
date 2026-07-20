"""PII masking middleware (spec section 5).

Detection is two-layered: regex for structured PII (emails, phones, credit
cards, IPs, SSN/Aadhaar/PAN-style IDs) -- fast, deterministic, always on --
plus Presidio (NER via spaCy) for freeform PII regex can't reliably catch
(names, locations). Masking is reversible tokenization (`<<PII_EMAIL_1>>`)
held in an in-memory map scoped to `trace_id` -- never persisted to logs or
traces. Only the Response Formatter may ask the vault to rehydrate, and only
for the original requesting user.
"""
import logging
import re
import threading
from pathlib import Path

import yaml

logger = logging.getLogger("pii")

_POLICY_PATH = Path(__file__).parent.parent / "config" / "pii_policy.yaml"

# Presidio/NER-detected entity types -- kept disjoint from the regex
# _PATTERNS below so the two layers never double-detect the same span.
_NER_ENTITIES = ["PERSON", "LOCATION"]

# contains_pii() (the output-guardrail leak scan) deliberately checks a
# narrower NER set than masking does: LOCATION is masked before reaching an
# LLM (still worth protecting on the way out to a cloud provider), but a
# formatted answer *naturally* mentions a city/country the user themselves
# asked about ("customers in Chennai") -- that's not a leak, and hard-
# blocking every location-bearing answer would make location queries
# unusable. PERSON stays in the leak scan: a name is far more uniquely
# identifying than a place, and answers legitimately needing to state a
# customer's name should have already rehydrated it via the vault by then,
# not produced it as a fresh, unmasked NER hit.
_NER_ENTITIES_FOR_LEAK_SCAN = ["PERSON"]

_presidio_analyzer = None
_presidio_unavailable = False
_presidio_lock = threading.Lock()


def _get_presidio_analyzer():
    """Lazily builds the Presidio AnalyzerEngine (loads a spaCy model, so
    this is deliberately deferred and cached). Degrades gracefully to
    regex-only detection if Presidio/spaCy isn't available -- this is an
    additive second layer, not a hard dependency."""
    global _presidio_analyzer, _presidio_unavailable
    if _presidio_unavailable:
        return None
    if _presidio_analyzer is None:
        with _presidio_lock:
            if _presidio_analyzer is None and not _presidio_unavailable:
                try:
                    from presidio_analyzer import AnalyzerEngine

                    _presidio_analyzer = AnalyzerEngine()
                except Exception as e:
                    logger.warning("presidio_unavailable, falling back to regex-only PII detection: %s", e)
                    _presidio_unavailable = True
                    return None
    return _presidio_analyzer

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

    return _mask_ner(masked, trace_id, entities, counters)


def _mask_ner(text: str, trace_id: str, entities_cfg: dict, counters: dict[str, int]) -> str:
    active = [e for e in _NER_ENTITIES if e in entities_cfg]
    if not active:
        return text
    analyzer = _get_presidio_analyzer()
    if analyzer is None:
        return text

    try:
        results = analyzer.analyze(text=text, language="en", entities=active)
    except Exception as e:
        logger.warning("presidio_analyze_failed: %s", e)
        return text

    # Replace back-to-front so earlier spans' offsets stay valid as later
    # (rightmost) ones are substituted first.
    for r in sorted(results, key=lambda r: r.start, reverse=True):
        cfg = entities_cfg.get(r.entity_type)
        if not cfg:
            continue
        original = text[r.start : r.end]
        if cfg.get("action") == "drop":
            text = text[: r.start] + text[r.end :]
            continue
        counters[r.entity_type] = counters.get(r.entity_type, 0) + 1
        token = f"<<{cfg.get('token_prefix', r.entity_type)}_{counters[r.entity_type]}>>"
        _vault.put(trace_id, token, original)
        text = text[: r.start] + token + text[r.end :]

    return text


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

    active = [e for e in _NER_ENTITIES_FOR_LEAK_SCAN if e in entities]
    analyzer = _get_presidio_analyzer() if active else None
    if analyzer is not None:
        try:
            if analyzer.analyze(text=text, language="en", entities=active):
                return True
        except Exception as e:
            logger.warning("presidio_analyze_failed: %s", e)

    return False
