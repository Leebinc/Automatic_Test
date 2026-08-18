from types import SimpleNamespace
from threading import Event

import pytest

import src.runner as runner_module
from src.runner import SimulationRunner
from src.tcp_telemetry_client import AsyncTelecommandError


def make_runner_for_interval(
    interval_sec: float,
    last_case_end_time: float | None,
) -> SimulationRunner:
    runner = SimulationRunner.__new__(SimulationRunner)
    runner.case_interval_sec = interval_sec
    runner._last_case_end_time = last_case_end_time
    return runner


def test_case_interval_waits_only_for_remaining_time(monkeypatch):
    runner = make_runner_for_interval(
        interval_sec=5.0,
        last_case_end_time=100.0,
    )
    sleeps = []
    monkeypatch.setattr(runner_module.time, "monotonic", lambda: 102.0)
    monkeypatch.setattr(runner_module.time, "sleep", sleeps.append)

    runner._wait_for_case_interval()

    assert sleeps == [3.0]


def test_first_case_does_not_wait(monkeypatch):
    runner = make_runner_for_interval(
        interval_sec=5.0,
        last_case_end_time=None,
    )
    sleeps = []
    monkeypatch.setattr(runner_module.time, "sleep", sleeps.append)

    runner._wait_for_case_interval()

    assert sleeps == []


def test_runner_notifies_before_execute_and_syncs_reset_between_them(monkeypatch):
    events = []

    class FakeTelecommandClient:
        def send_async_telecommand_request(self, **request):
            events.append((request["operation"], request["command_code"]))
            return {
                "requestId": f"{request['operation']}-{request['command_code']}",
                "code": 0,
                "msg": "success",
            }

    class FakeUdpClient:
        def send_initial_condition(self, condition, reset):
            events.append(("udp-reset", reset))
            return b"reset"

    runner = SimulationRunner.__new__(SimulationRunner)
    runner.telecommand_config = {
        "enabled": True,
        "channel": "CH1",
        "notification_timeout_sec": 3.0,
        "execution_timeout_sec": 5.0,
        "interval_sec": 0.0,
        "post_delay_sec": 0.0,
    }
    runner.telecommand_client = FakeTelecommandClient()
    runner.udp_client = FakeUdpClient()
    monkeypatch.setattr(runner_module.time, "sleep", lambda _: None)
    sim_case = SimpleNamespace(
        case_id="case",
        initial_condition=object(),
        telecommand={
            "command_codes": [
                "K3072",
                {"command_code": "ZS300", "sync_reset_start": True},
            ]
        },
    )

    reset_sent = runner._send_telecommand_if_configured(sim_case)

    assert reset_sent
    assert events == [
        ("notify", "K3072"),
        ("execute", "K3072"),
        ("notify", "ZS300"),
        ("udp-reset", True),
        ("execute", "ZS300"),
    ]


def test_runner_does_not_execute_after_notification_failure(monkeypatch):
    events = []

    class FailingTelecommandClient:
        def send_async_telecommand_request(self, **request):
            events.append(request["operation"])
            raise AsyncTelecommandError("notify failed")

    runner = SimulationRunner.__new__(SimulationRunner)
    runner.telecommand_config = {
        "enabled": True,
        "notification_timeout_sec": 3.0,
        "execution_timeout_sec": 5.0,
        "interval_sec": 0.0,
    }
    runner.telecommand_client = FailingTelecommandClient()
    monkeypatch.setattr(runner_module.time, "sleep", lambda _: None)
    sim_case = SimpleNamespace(
        case_id="case",
        initial_condition=object(),
        telecommand={"command_codes": ["K3036"]},
    )

    with pytest.raises(AsyncTelecommandError, match="notify failed"):
        runner._send_telecommand_if_configured(sim_case)

    assert events == ["notify"]


def test_runner_stops_case_after_execution_failure(monkeypatch):
    events = []

    class ExecutionFailingTelecommandClient:
        def send_async_telecommand_request(self, **request):
            events.append((request["operation"], request["command_code"]))
            if request["operation"] == "execute":
                raise AsyncTelecommandError("execute failed")
            return {
                "requestId": "notify-success",
                "code": 0,
                "msg": "Channel notified",
            }

    runner = SimulationRunner.__new__(SimulationRunner)
    runner.telecommand_config = {
        "enabled": True,
        "notification_timeout_sec": 3.0,
        "execution_timeout_sec": 5.0,
        "interval_sec": 0.0,
    }
    runner.telecommand_client = ExecutionFailingTelecommandClient()
    monkeypatch.setattr(runner_module.time, "sleep", lambda _: None)
    sim_case = SimpleNamespace(
        case_id="case",
        initial_condition=object(),
        telecommand={"command_codes": ["K3036", "SHOULD_NOT_RUN"]},
    )

    with pytest.raises(AsyncTelecommandError, match="execute failed"):
        runner._send_telecommand_if_configured(sim_case)

    assert events == [
        ("notify", "K3036"),
        ("execute", "K3036"),
    ]


def test_runner_passes_stop_event_to_telemetry_client_and_restores_codes():
    received = {}

    class FakeTelemetryClient:
        def __init__(self):
            self.telemetry_poll_codes = ["DEFAULT"]

        def receive_frames(self, **kwargs):
            received.update(kwargs)
            return iter(())

    runner = SimulationRunner.__new__(SimulationRunner)
    runner.tcp_client = FakeTelemetryClient()
    runner.max_duration_sec = 300.0
    stop_event = Event()
    sim_case = SimpleNamespace(telemetry_codes=["ROLL", "PITCH"])

    assert list(runner._receive_frames(sim_case, stop_event=stop_event)) == []
    assert received == {
        "max_duration_sec": 300.0,
        "stop_event": stop_event,
    }
    assert runner.tcp_client.telemetry_poll_codes == ["DEFAULT"]
