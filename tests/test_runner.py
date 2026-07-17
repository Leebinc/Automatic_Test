import src.runner as runner_module
from src.runner import SimulationRunner


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
