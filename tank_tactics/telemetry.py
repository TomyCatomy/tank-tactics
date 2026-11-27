"""OpenTelemetry export of traces, metrics and logs over OTLP/HTTP (for example to Grafana Cloud).

Off unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set. Everything else is the SDK's standard
``OTEL_*`` variables, which its exporters read themselves: ``OTEL_EXPORTER_OTLP_HEADERS`` for
the auth header, ``OTEL_SERVICE_NAME`` (default ``tank-tactics``), ``OTEL_RESOURCE_ATTRIBUTES``,
``OTEL_TRACES_SAMPLER``, ``OTEL_METRIC_EXPORT_INTERVAL`` and so on.

Sent: a span per HTTP request and WebSocket connection (none for static files or WebSocket
messages), request metrics by route, the ``tank_tactics.*`` metrics below and INFO+ log records.
Headers and bodies (tokens, chat text, names) are never recorded, and ``redaction.py`` masks
secrets in what is (URL query strings, exception messages and tracebacks, log messages).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, Mapping

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

from . import game, redaction

ENDPOINT_VAR = "OTEL_EXPORTER_OTLP_ENDPOINT"
DEFAULT_SERVICE_NAME = "tank-tactics"
EXCLUDED_URLS = "/static/"  # regexes searched in the full URL, comma-separated


def enabled(env: Mapping[str, str] = os.environ) -> bool:
    return bool(env.get(ENDPOINT_VAR, "").strip())


@dataclass
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    log_handler: LoggingHandler

    def shutdown(self) -> None:
        """Flush whatever is still buffered and stop exporting."""
        logging.getLogger().removeHandler(self.log_handler)
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            provider.shutdown()


def setup(
    app: FastAPI,
    env: Mapping[str, str] = os.environ,
    *,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
    log_exporter: LogRecordExporter | None = None,
    install_globals: bool = True,
) -> Telemetry | None:
    """Instrument ``app`` (built by ``create_app``) before it serves anything; ``None`` when off.
    Tests pass in-memory exporters (exported synchronously) and ``install_globals=False``."""
    if not enabled(env) and span_exporter is None:
        return None
    resource = _resource()
    redaction.register_env_secrets(env)

    tracer_provider = TracerProvider(resource=resource)
    if span_exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(BatchSpanProcessor(redaction.RedactingSpanExporter(OTLPSpanExporter())))
    else:
        tracer_provider.add_span_processor(SimpleSpanProcessor(redaction.RedactingSpanExporter(span_exporter)))

    if metric_reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(redaction.RedactingLogProcessor())  # before exporting
    if log_exporter is None:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    else:
        logger_provider.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
    log_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logging.getLogger().addHandler(log_handler)

    if install_globals:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        set_logger_provider(logger_provider)

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        excluded_urls=EXCLUDED_URLS,
        exclude_spans=["receive", "send"],  # otherwise every WebSocket push is a span
    )
    _game_metrics(app, meter_provider)
    return Telemetry(tracer_provider, meter_provider, logger_provider, log_handler)


def _resource() -> Resource:
    resource = Resource.create()  # reads OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES
    if str(resource.attributes.get(SERVICE_NAME, "")).startswith("unknown_service"):
        resource = resource.merge(Resource({SERVICE_NAME: DEFAULT_SERVICE_NAME}))
    return resource


def _game_metrics(app: FastAPI, meter_provider: MeterProvider) -> None:
    """Gauges for live games and open WebSockets, and a counter of game log events by type."""
    service, hub = app.state.service, app.state.hub
    meter = meter_provider.get_meter("tank_tactics")

    def games(_: CallbackOptions) -> Iterable[Observation]:
        counts = {"lobby": 0, "active": 0}
        for state in list(service.games.values()):  # list() copies under the GIL; safe off-thread
            if state.status in counts:
                counts[state.status] += 1
        return [Observation(n, {"status": status}) for status, n in counts.items()]

    def sockets(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(sum(len(s) for s in list(hub.sockets.values())))]

    meter.create_observable_gauge("tank_tactics.games", [games], unit="{game}", description="Live games by status.")
    meter.create_observable_gauge(
        "tank_tactics.websockets", [sockets], unit="{connection}", description="Open live-update connections."
    )
    events = meter.create_counter(
        "tank_tactics.game_events", unit="{event}", description="Game log entries written, by event type."
    )

    def count(_game_id: str, entries: list[game.LogEntry]) -> None:
        for entry in entries:
            events.add(1, {"event_type": entry.event_type.value})

    hub.listeners.append(count)
