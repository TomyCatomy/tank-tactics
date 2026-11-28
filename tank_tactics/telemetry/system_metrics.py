"""Host and process load: CPU, memory, disk and network, read with psutil.

Most of it comes from OpenTelemetry's ``SystemMetricsInstrumentor`` (semantic-convention
names such as ``system.cpu.utilization`` and ``system.network.io``), limited to the metrics
and states in ``CONFIG`` so the number of series stays small. It has no filesystem or load
average metrics, so those are added here under the names the OpenTelemetry Collector's
``hostmetrics`` receiver uses (``system.filesystem.*``, ``system.cpu.load_average.*``).
"""
from __future__ import annotations

import os
from typing import Iterable

import psutil
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider

CONFIG: dict[str, list[str] | None] = {
    "system.cpu.utilization": ["user", "system", "iowait", "idle"],  # iowait is Linux only
    "system.memory.usage": ["used", "free", "cached", "available"],
    "system.memory.utilization": ["used"],
    "system.swap.utilization": ["used"],
    "system.disk.io": ["read", "write"],
    "system.disk.operations": ["read", "write"],
    "system.disk.time": ["read", "write"],
    "system.network.io": ["transmit", "receive"],
    "system.network.packets": ["transmit", "receive"],
    "system.network.errors": ["transmit", "receive"],
    "system.network.dropped.packets": ["transmit", "receive"],
    "process.cpu.utilization": None,
    "process.memory.usage": None,
    "process.thread.count": None,
}
FILESYSTEM_PATH = os.path.abspath(os.sep)  # the root volume, where the server and its data live


class SystemMetrics:
    """Starts reporting into ``meter_provider``; ``stop`` lets a later ``start`` report again
    (the instrumentor is a process-wide singleton that instruments once)."""

    def __init__(self, meter_provider: MeterProvider):
        self._instrumentor = SystemMetricsInstrumentor(config=CONFIG)
        self._instrumentor.instrument(meter_provider=meter_provider)
        _host_extras(meter_provider)

    def stop(self) -> None:
        self._instrumentor.uninstrument()


def _host_extras(meter_provider: MeterProvider) -> None:
    meter = meter_provider.get_meter("tank_tactics.system")

    def usage(_: CallbackOptions) -> Iterable[Observation]:
        disk = psutil.disk_usage(FILESYSTEM_PATH)
        where = {"mountpoint": FILESYSTEM_PATH}
        return [Observation(disk.used, {**where, "state": "used"}), Observation(disk.free, {**where, "state": "free"})]

    def utilization(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(psutil.disk_usage(FILESYSTEM_PATH).percent / 100, {"mountpoint": FILESYSTEM_PATH})]

    meter.create_observable_gauge(
        "system.filesystem.usage", [usage], unit="By", description="Space used and free on the root filesystem."
    )
    meter.create_observable_gauge(
        "system.filesystem.utilization", [utilization], unit="1", description="Fraction of the root filesystem used."
    )
    for i, minutes in enumerate(("1m", "5m", "15m")):
        meter.create_observable_gauge(
            f"system.cpu.load_average.{minutes}",
            [lambda _, i=i: [Observation(psutil.getloadavg()[i])]],
            unit="{thread}",
            description=f"Average number of runnable threads over the last {minutes}.",
        )
