"""Masks secrets in telemetry before it leaves the process (see ``telemetry``).

The first line of defence is not recording sensitive data at all: no request or response
bodies or headers. This is the second, for what still slips into text: query strings
(Discord's OAuth ``code`` and ``state`` come back in one), exception messages and
tracebacks (a database error can quote its connection URL, an HTTP error a webhook URL),
and log messages. Every string in a span (attributes, events, status) and in a log record
(body and attributes) goes through ``redact``, and URL attributes lose their query string.

``redact`` masks, by shape:

- the values of secret-looking keys, as ``key=value``, ``key: value`` or ``"key": "value"``
  (tokens, codes, states, secrets, passwords, API keys, and a rejoin link's ``t``);
- passwords in URLs (``scheme://user:password@host``);
- Discord webhook tokens (``/api/webhooks/<id>/<token>``);
- ``Bearer`` / ``Basic`` credentials and JWTs (Google ID tokens);
- any value registered with ``register_secret`` (the server registers its configured
  secrets, such as the Discord client secret, at startup).
"""
from __future__ import annotations

import re
import threading
from typing import Any, Mapping, Sequence

from opentelemetry.sdk._logs import LogRecordProcessor, ReadWriteLogRecord
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status

MASK = "[REDACTED]"

_KEYS = (r"(?:access_|id_|refresh_|seat_|session_)?token|code|state|(?:client_)?secret|password|passwd"
         r"|api_?key|authorization|cookie|t")
_PATTERNS = [
    # First, so "Authorization: Bearer <token>" doesn't stop at "Bearer" below.
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[\w.~+/=-]+"), rf"\g<1> {MASK}"),
    # "key": "value", key=value, key: value (not when the value is an object or list)
    (re.compile(rf"""(?i)(?<![\w-])(["']?(?:{_KEYS})["']?\s*[:=]\s*["']?)(?![\s{{\[])[^"'\s,&;#}}\]]+"""),
     rf"\g<1>{MASK}"),
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@"), rf"\g<1>{MASK}@"),
    (re.compile(r"(?i)(/api/(?:v\d+/)?webhooks/\d+/)[\w-]+"), rf"\g<1>{MASK}"),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), MASK),
]
URL_ATTRIBUTES = frozenset({"http.url", "http.target", "url.full", "url.original"})
DROPPED_ATTRIBUTES = frozenset({"url.query"})

_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str | None) -> None:
    """Mask this exact value wherever it appears. Short values are ignored, since masking
    them would wreck ordinary text and they aren't real secrets anyway."""
    if value and len(value) >= 8:
        with _lock:
            _secrets.add(value)


def redact(text: str) -> str:
    for value in sorted(_secrets, key=len, reverse=True):
        text = text.replace(value, MASK)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def value(v: Any) -> Any:
    """``redact`` applied to a telemetry attribute value (a string or a sequence of them)."""
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, (list, tuple)):
        return type(v)(value(x) for x in v)
    return v


def attributes(attrs: Any) -> dict[str, Any]:
    """A redacted copy of span or log attributes."""
    out = {}
    for key, v in dict(attrs or {}).items():
        if key in DROPPED_ATTRIBUTES:
            continue
        if key in URL_ATTRIBUTES and isinstance(v, str):
            v = v.split("?", 1)[0]
        out[key] = value(v)
    return out


# == OpenTelemetry hooks =================================================================

class RedactingSpanExporter(SpanExporter):
    """Exports redacted copies of finished spans through ``inner``."""

    def __init__(self, inner: SpanExporter):
        self.inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self.inner.export([_span(s) for s in spans])

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


def _span(s: ReadableSpan) -> ReadableSpan:
    status = s.status
    if status.description:
        status = Status(status.status_code, redact(status.description))
    return ReadableSpan(
        name=s.name, context=s.context, parent=s.parent, resource=s.resource,
        attributes=attributes(s.attributes), kind=s.kind, links=s.links, status=status,
        events=[Event(e.name, attributes(e.attributes), e.timestamp) for e in s.events],
        start_time=s.start_time, end_time=s.end_time, instrumentation_scope=s.instrumentation_scope,
    )


class RedactingLogProcessor(LogRecordProcessor):
    """Redacts each log record in place; add it before the exporting processor."""

    def on_emit(self, log_record: ReadWriteLogRecord) -> None:
        record = log_record.log_record
        if isinstance(record.body, str):
            record.body = redact(record.body)
        record.attributes = attributes(record.attributes)
        record.exception = None  # its text is in the (now redacted) exception.* attributes

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


_SECRET_VAR = re.compile(r"SECRET|PASSWORD|TOKEN|_KEY$|_HEADERS$|DATABASE_URL")


def register_env_secrets(env: Mapping[str, str]) -> None:
    """Register the values of secret-looking environment variables, such as
    ``DISCORD_CLIENT_SECRET`` and ``OTEL_EXPORTER_OTLP_HEADERS`` (and each header value in it)."""
    for name, v in env.items():
        if _SECRET_VAR.search(name.upper()):
            register_secret(v)
            if name.upper().endswith("_HEADERS"):
                for header in v.split(","):
                    register_secret(header.partition("=")[2].strip())
