"""Tracing/logging middleware (spec section 7).

Wraps every LangGraph node with an OTel span plus one structured JSON log
line. Every log line carries the request's trace_id so a full run can be
reconstructed end-to-end by filtering app.log.
"""
import functools
import logging
import os
import time
from pathlib import Path

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_provider = TracerProvider(resource=Resource.create({"service.name": "nl2sql-agents"}))

# If OTEL_EXPORTER_OTLP_ENDPOINT is set (see infra/docker-compose.yml's
# otel-collector, default http://localhost:4317), export real spans there
# so they show up in Grafana Tempo. Otherwise fall back to a local file --
# BatchSpanProcessor swallows/retries export failures internally, so an
# unreachable collector never breaks the app, it just drops spans.
_otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
if _otlp_endpoint:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_otlp_endpoint, insecure=True)))
else:
    _span_file = open(LOG_DIR / "spans.jsonl", "a")
    _provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter(out=_span_file)))

trace.set_tracer_provider(_provider)
_tracer = trace.get_tracer("nl2sql-agents")

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.WriteLoggerFactory(file=open(LOG_DIR / "app.log", "a")),
)
_logger = structlog.get_logger("app")

logging.basicConfig(level=logging.INFO)


def traced_node(fn):
    """Decorator applied to every agent's invoke(state) -> state.

    Order relative to other decorators (outer to inner):
    resilient_node(traced_node(pii_guarded(fn)))
    so retries are re-traced per attempt and PII masking happens closest
    to the agent logic.
    """

    @functools.wraps(fn)
    def wrapper(state, *args, **kwargs):
        node_name = fn.__name__
        trace_id = state.get("trace_id", "unknown")
        attempt = len(state.get("sql_attempts", [])) + 1

        with _tracer.start_as_current_span(node_name) as span:
            span.set_attribute("trace_id", trace_id)
            span.set_attribute("attempt", attempt)
            start = time.perf_counter()
            status = "success"
            error = None
            result = None
            try:
                result = fn(state, *args, **kwargs)
                if isinstance(result, dict) and result.get("status") == "error":
                    status = "error"
                    error = result.get("error_detail")
                return result
            except Exception as e:
                status = "error"
                error = str(e)
                span.record_exception(e)
                raise
            finally:
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                span.set_attribute("duration_ms", duration_ms)
                span.set_attribute("status", status)

                # sql_generator sets _generation_mode ("initial"/"regenerate")
                # since it's one node for both -- surface it here so a log
                # line still says which mode produced a given attempt.
                mode = result.get("_generation_mode") if isinstance(result, dict) else None
                last_attempt = (
                    (result.get("sql_attempts") or [None])[-1]
                    if isinstance(result, dict)
                    else None
                )
                is_validator_step = node_name == "sql_validator"
                validator_error = last_attempt["error"] if (last_attempt and is_validator_step) else None
                sql_valid = last_attempt["valid"] if (last_attempt and is_validator_step) else None

                if mode:
                    span.set_attribute("mode", mode)
                if validator_error:
                    span.set_attribute("validator_error", validator_error)
                if sql_valid is not None:
                    span.set_attribute("sql_valid", sql_valid)

                _logger.info(
                    "agent_step",
                    trace_id=trace_id,
                    step=node_name,
                    attempt=attempt,
                    mode=mode,
                    status=status,
                    latency_ms=duration_ms,
                    error=error,
                    validator_error=validator_error,
                    sql_valid=sql_valid,
                )

    return wrapper
