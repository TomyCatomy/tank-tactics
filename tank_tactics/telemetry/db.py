"""Database client telemetry from OpenTelemetry's SQLAlchemy instrumentation.

Every query the store runs becomes a client span (``db.system`` ``postgresql`` on Neon or
``sqlite``, the server address, database name and the SQL text). The SQL is the
statement with placeholders, since the store only binds parameters, so no values
(tokens, names, chat text) are recorded, and the redacting exporter masks anything
secret-looking anyway. The pool's ``db.client.connections.usage`` gauge (idle / used)
shows whether Neon connections are running out. Instrumenting it here, rather than the
psycopg driver, covers both backends and the pool.
"""
from __future__ import annotations

from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy.engine import Engine


class DatabaseTelemetry:
    """Instruments ``engine`` until ``stop`` (the instrumentor is a process-wide singleton)."""

    def __init__(self, engine: Engine, tracer_provider: TracerProvider, meter_provider: MeterProvider):
        self._instrumentor = SQLAlchemyInstrumentor()
        # It declares SQLAlchemy < 2.1 but only uses the engine event API, which 2.1 keeps;
        # test_telemetry.py checks the spans and metrics still arrive.
        self._instrumentor.instrument(
            engine=engine, tracer_provider=tracer_provider, meter_provider=meter_provider, skip_dep_check=True
        )

    def stop(self) -> None:
        self._instrumentor.uninstrument()
