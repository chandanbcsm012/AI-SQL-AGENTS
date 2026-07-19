"""Technical resilience layer: wraps every LangGraph node function.

This is independent of the business-level "invalid SQL" retry (see
agents/sql_validator.py + graph.py conditional edges). This decorator only
reacts to *exceptions* raised by a node (timeouts, connection errors,
malformed provider responses, 5xx/429) -- never to a semantically invalid
SQL statement, which is not an exception.
"""
import functools
import logging
import time

from model_factory import FALLBACK_PROVIDER

logger = logging.getLogger("resilience")


def resilient_node(max_attempts: int = 3, base_delay: float = 1.5):
    """Retries transient errors with exponential backoff, then attempts
    self-recovery by swapping to the fallback model provider for one last
    try, then surfaces a structured error on the state instead of raising.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state, *args, **kwargs):
            last_err = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(state, *args, **kwargs)
                except Exception as e:
                    last_err = e
                    logger.warning(
                        "node_retry",
                        extra={
                            "trace_id": state.get("trace_id"),
                            "node": fn.__name__,
                            "attempt": attempt,
                            "error": str(e),
                        },
                    )
                    if attempt < max_attempts:
                        time.sleep(base_delay * (2 ** (attempt - 1)))

            try:
                logger.warning(
                    "node_self_recovery",
                    extra={
                        "trace_id": state.get("trace_id"),
                        "node": fn.__name__,
                        "fallback_provider": FALLBACK_PROVIDER,
                    },
                )
                state["_force_provider"] = FALLBACK_PROVIDER
                return fn(state, *args, **kwargs)
            except Exception as e2:
                logger.error(
                    "node_failed_permanently",
                    extra={
                        "trace_id": state.get("trace_id"),
                        "node": fn.__name__,
                        "error": str(e2),
                    },
                )
                state["status"] = "error"
                state["error_detail"] = {
                    "node": fn.__name__,
                    "error": str(e2),
                    "prior_error": str(last_err),
                }
                return state

        return wrapper

    return decorator
