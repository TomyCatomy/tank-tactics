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


def test_host_load_metrics(instrumented):
    """CPU, memory, disk and network load are reported, every time telemetry is set up
    (the system metrics instrumentor is a process-wide singleton)."""
    *_, reader, _ = instrumented
    points = _metrics(reader)
    for name in ("system.cpu.utilization", "system.cpu.load_average.1m", "system.memory.usage",
                 "system.memory.utilization", "system.filesystem.usage", "system.filesystem.utilization",
                 "system.disk.io", "system.network.io", "process.cpu.utilization", "process.memory.usage"):
        assert points.get(name), f"{name} was not reported"
    assert 0 < points["system.filesystem.utilization"][0].value < 1
    assert {p.attributes["state"] for p in points["system.memory.usage"]} >= {"used", "free"}
    assert {p.attributes["direction"] for p in points["system.network.io"]} == {"transmit", "receive"}
    assert points["process.memory.usage"][0].value > 0
    assert "system.network.connections" not in points  # left out: slow, and many series


def test_log_records_are_exported(instrumented):
    *_, logs = instrumented
    logging.getLogger("tank_tactics").warning("scheduler tick failed")
    assert any(r.log_record.body == "scheduler tick failed" for r in logs.get_finished_logs())


FAKE = {
    "oauth": "OAUTHCODE123", "state": "STATESTRING456", "webhook": "WEBHOOKTOKEN789",
    "db": "DBPASSWORD000", "bearer": "BEARERTOKEN111", "client_secret": "CLIENTSECRET222",
    "jwt": "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl",
}


def test_secrets_never_reach_the_exporters():
    """Plants fake secrets everywhere telemetry could pick them up and checks every
    exported span and log record."""
    svc, _ = make_service()
    app = create_app(svc)

    @app.get("/api/test-boom")
    def boom():
        raise RuntimeError(f"connect to postgresql://app:{FAKE['db']}@db.example/tt failed")

    spans, logs = InMemorySpanExporter(), InMemoryLogRecordExporter()
    tel = telemetry.setup(app, env={"DISCORD_CLIENT_SECRET": FAKE["client_secret"]}, span_exporter=spans,
                          metric_reader=InMemoryMetricReader(), log_exporter=logs, install_globals=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/api/discord/callback", params={"code": FAKE["oauth"], "state": FAKE["state"]},
                   follow_redirects=False)
        assert client.get("/api/test-boom").status_code == 500
    log = logging.getLogger("tank_tactics")
    log.warning("discord post to https://discord.com/api/webhooks/42/%s failed", FAKE["webhook"])
    log.warning("auth header was Bearer %s with id token %s", FAKE["bearer"], FAKE["jwt"])
    log.warning("token exchange refused: client_secret=%s", FAKE["client_secret"])
    try:
        raise ValueError(f"bad secret {FAKE['client_secret']} for https://discord.com/api/webhooks/1/{FAKE['webhook']}")
    except ValueError:
        log.exception("startup check failed")
    tel.shutdown()

    finished = spans.get_finished_spans()
    assert any(e.name == "exception" for s in finished for e in s.events), "the 500 left no exception event"
    exported = [s.to_json() for s in finished]
    exported += [json.dumps({"body": str(r.log_record.body), "attributes": dict(r.log_record.attributes or {})})
                 for r in logs.get_finished_logs()]
    dumped = "\n".join(exported)
    assert "[REDACTED]" in dumped and "startup check failed" in dumped
    for name, secret in FAKE.items():
        assert secret not in dumped, f"the fake {name} reached the exporter"
