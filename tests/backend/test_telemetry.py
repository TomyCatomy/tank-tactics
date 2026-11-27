"""OpenTelemetry export: off by default; when on, traces, metrics and logs carry no secrets."""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tank_tactics import telemetry
from tank_tactics.api import create_app

from ..support import make_service, setup_game


def test_off_without_an_endpoint():
    svc, _ = make_service()
    assert telemetry.setup(create_app(svc), env={}) is None
    assert telemetry.setup(create_app(svc), env={"OTEL_EXPORTER_OTLP_ENDPOINT": " "}) is None
    assert telemetry.enabled({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.example/otlp"})


@pytest.fixture
def instrumented():
    svc, _ = make_service(action_rate_per_second=100)
    app = create_app(svc)
    spans, reader, logs = InMemorySpanExporter(), InMemoryMetricReader(), InMemoryLogRecordExporter()
    tel = telemetry.setup(app, env={}, span_exporter=spans, metric_reader=reader, log_exporter=logs,
                          install_globals=False)
    assert tel is not None
    with TestClient(app) as client:
        yield client, svc, spans, reader, logs
    tel.shutdown()


def _metrics(reader: InMemoryMetricReader) -> dict[str, list]:
    out: dict[str, list] = {}
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                out.setdefault(m.name, []).extend(m.data.data_points)
    return out


def test_requests_are_traced_by_route_without_static_files(instrumented):
    client, svc, spans, _, _ = instrumented
    gid = client.post("/api/games", json={"name": "Ann"}).json()["game_id"]
    assert client.get(f"/api/games/{gid}").status_code == 200
    assert client.get("/static/css/style.css").status_code in (200, 404)

    names = [s.name for s in spans.get_finished_spans()]
    assert "POST /api/games" in names and "GET /api/games/{game_id}" in names
    assert not any("/static" in n for n in names)
    resource = spans.get_finished_spans()[0].resource.attributes
    assert resource["service.name"] == "tank-tactics"


def test_spans_and_logs_carry_no_tokens_or_bodies(instrumented):
    client, _, spans, _, logs = instrumented
    host = client.post("/api/games", json={"name": "Secret Name"}).json()
    sent = client.post(f"/api/games/{host['game_id']}/chat", json={"text": "hidden words"},
                       headers={"Authorization": f"Bearer {host['token']}"})
    assert sent.status_code == 201
    dumped = json.dumps([dict(s.attributes or {}) for s in spans.get_finished_spans()])
    dumped += json.dumps([str(r.log_record.body) for r in logs.get_finished_logs()])
    for secret in (host["token"], "Secret Name", "hidden words"):
        assert secret not in dumped


def test_game_metrics(instrumented):
    client, svc, _, reader, _ = instrumented
    gid, _, creds = setup_game(svc)
    client.post("/api/games", json={"name": "Lobby host"})
    with client.websocket_connect(f"/ws/{gid}") as ws:
        ws.send_json({"type": "auth", "token": creds[0]["token"]})
        assert ws.receive_json()["type"] == "state"
        points = _metrics(reader)

    games = {p.attributes["status"]: p.value for p in points["tank_tactics.games"]}
    assert games == {"lobby": 1, "active": 1}
    assert [p.value for p in points["tank_tactics.websockets"]] == [1]
    events = {p.attributes["event_type"]: p.value for p in points["tank_tactics.game_events"]}
    assert events["game_start"] == 1 and events["join"] >= 2
    assert any(name.startswith("http.server") for name in points)


def test_log_records_are_exported(instrumented):
    *_, logs = instrumented
    logging.getLogger("tank_tactics").warning("scheduler tick failed")
    assert any(r.log_record.body == "scheduler tick failed" for r in logs.get_finished_logs())
