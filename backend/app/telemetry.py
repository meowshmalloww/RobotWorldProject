"""Telemetry: OpenTelemetry -> self-hosted SigNoz (OTLP HTTP/protobuf) + local mirror.

Every span/log/metric is written to the local SQLite store (powering the
Observability console and the agent's failure-analysis queries) AND exported
to self-hosted SigNoz when its OTLP endpoint is enabled. Community OTLP
ingestion does not require a cloud key; query credentials remain separate.
Runtime attachment is
supported — OTLP processors are attached to the live providers.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor
from opentelemetry.trace import Status, StatusCode

from . import __version__
from .db import SessionLocal
from .models import LogLine, MetricPoint, Span

SERVICE_NAME = "robotworld-backend"

_span_q: "queue.SimpleQueue[dict]" = queue.SimpleQueue()
_log_q: "queue.SimpleQueue[dict]" = queue.SimpleQueue()
_metric_q: "queue.SimpleQueue[dict]" = queue.SimpleQueue()

_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_logger_provider: LoggerProvider | None = None
_otlp_attached = False


class _OtelInternalFilter(logging.Filter):
    """Keep exporter failures out of the exporter itself.

    Without this filter an OTLP transport error is handled by the root
    LoggingHandler, submitted to the same unavailable OTLP endpoint, and can
    create an unbounded feedback loop. Application logs remain unaffected.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry.")


def _otlp_handler(provider: LoggerProvider) -> LoggingHandler:
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    handler.addFilter(_OtelInternalFilter())
    return handler


class LocalSpanProcessor(SpanProcessor):
    """Mirror finished spans into the local store."""

    def on_end(self, span) -> None:  # noqa: ANN001
        try:
            ctx = span.get_span_context()
            parent = span.parent.span_id if span.parent else None
            attrs = {k: v for k, v in (span.attributes or {}).items() if isinstance(v, (str, int, float, bool))}
            _span_q.put(
                {
                    "trace_id": format(ctx.trace_id, "032x"),
                    "span_id": format(ctx.span_id, "016x"),
                    "parent_id": format(parent, "016x") if parent else None,
                    "name": span.name,
                    "service": (span.resource.attributes.get("service.name") if span.resource else SERVICE_NAME) or SERVICE_NAME,
                    "start_ms": (span.start_time or 0) / 1e6,
                    "duration_ms": ((span.end_time or 0) - (span.start_time or 0)) / 1e6,
                    "status": "error" if span.status.status_code == StatusCode.ERROR else "ok",
                    "attrs": attrs,
                }
            )
        except Exception:  # telemetry must never break the app
            pass


class LocalLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _log_q.put(
                {
                    "time_ms": record.created * 1000,
                    "level": record.levelname if record.levelname in ("INFO", "WARN", "ERROR", "DEBUG") else "INFO",
                    "service": getattr(record, "service", SERVICE_NAME),
                    "message": record.getMessage()[:2000],
                    "trace_id": getattr(record, "otelTraceID", None),
                }
            )
        except Exception:
            pass


def _resource() -> Resource:
    return Resource.create({"service.name": SERVICE_NAME, "service.version": __version__})


def init_otel(signoz_endpoint: str | None = None) -> None:
    """Create providers once. If SigNoz is already configured at call time,
    all three OTLP exporters are attached (metric readers can only be added
    at MeterProvider construction, so this is the startup path)."""
    global _tracer_provider, _meter_provider, _logger_provider, _otlp_attached
    if _tracer_provider is not None:
        return
    _tracer_provider = TracerProvider(resource=_resource())
    _tracer_provider.add_span_processor(LocalSpanProcessor())
    trace.set_tracer_provider(_tracer_provider)

    _logger_provider = LoggerProvider(resource=_resource())

    if signoz_endpoint:
        base = signoz_endpoint.rstrip("/")
        _tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))
        )
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{base}/v1/metrics"), export_interval_millis=30000
        )
        _meter_provider = MeterProvider(resource=_resource(), metric_readers=[reader])
        _logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs"))
        )
        logging.getLogger().addHandler(_otlp_handler(_logger_provider))
        _otlp_attached = True
    else:
        _meter_provider = MeterProvider(resource=_resource())

    metrics.set_meter_provider(_meter_provider)

    handler = LocalLogHandler()
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


async def configure_signoz(endpoint: str | None) -> bool:
    """Attach trace/log OTLP exporters at runtime (metrics reader attaches on
    next process start — a MeterProvider limitation in OTel Python)."""
    global _otlp_attached
    if _otlp_attached or not endpoint:
        return _otlp_attached
    init_otel()
    base = endpoint.rstrip("/")
    try:
        _tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))
        )
        _logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs"))
        )
        logging.getLogger().addHandler(_otlp_handler(_logger_provider))
        _otlp_attached = True
    except Exception:
        logging.getLogger(__name__).exception("Failed to attach SigNoz OTLP exporters")
    return _otlp_attached


def signoz_exporting() -> bool:
    return _otlp_attached


def tracer(name: str = SERVICE_NAME):
    init_otel()
    return trace.get_tracer(name, __version__)


@contextmanager
def span(name: str, service: str | None = None, **attrs: Any) -> Iterator:
    """Start a span with safe attribute values (used across the pipeline)."""
    tr = tracer(service or SERVICE_NAME)
    with tr.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if isinstance(v, (str, int, float, bool)):
                sp.set_attribute(k, v)
        try:
            yield sp
        except Exception as exc:
            sp.record_exception(exc)
            sp.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise


def emit_metric(name: str, value: float, **attrs: Any) -> None:
    init_otel()
    clean = {k: v for k, v in attrs.items() if isinstance(v, (str, int, float, bool))}
    _metric_q.put({"ts_ms": time.time() * 1000, "name": name, "value": float(value), "attrs": clean})
    try:
        counter = _counters.get(name)
        if counter is None:
            counter = _meter_provider.get_meter(SERVICE_NAME).create_counter(name)
            _counters[name] = counter
        counter.add(float(value), clean)
    except Exception:
        pass


_counters: dict[str, Any] = {}


async def drain_loop(stop: asyncio.Event) -> None:
    """Background task: flush queued telemetry into SQLite."""
    while not stop.is_set():
        try:
            await _drain_once()
        except Exception:
            logging.getLogger(__name__).exception("telemetry drain failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.6)
        except asyncio.TimeoutError:
            pass
    await _drain_once()


async def _drain_once() -> None:
    spans: list[dict] = []
    logs: list[dict] = []
    metrics_rows: list[dict] = []
    for q, buf in ((_span_q, spans), (_log_q, logs), (_metric_q, metrics_rows)):
        while True:
            try:
                buf.append(q.get_nowait())
            except queue.Empty:
                break
    if not (spans or logs or metrics_rows):
        return
    async with SessionLocal() as s:
        s.add_all([Span(**row) for row in spans])
        s.add_all([LogLine(**row) for row in logs])
        s.add_all([MetricPoint(**row) for row in metrics_rows])
        await s.commit()
