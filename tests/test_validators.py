import pytest

from src.models import TelemetryFrame
from tests.validators import (
    CheckState,
    assert_checks_completed,
    frame_value,
    should_start_judgment,
    update_check,
    update_convergence_check,
    update_control_mode_check,
)


def make_frame(
    timestamp_sec: float,
    attitude_angle_deg: list[float] | None = None,
    control_mode: str = "3",
    attitude_reference: str = "3",
    values: dict | None = None,
) -> TelemetryFrame:
    return TelemetryFrame(
        case_id="unit_case",
        timestamp_sec=timestamp_sec,
        attitude_angle_deg=attitude_angle_deg or [],
        angular_rate_deg_s=None,
        control_mode=control_mode,
        attitude_reference=attitude_reference,
        sim_status="RUNNING",
        raw={},
        values=values or {},
    )


def convergence_check(**overrides) -> dict:
    check = {
        "type": "convergence",
        "name": "generic convergence",
        "fields": ["A", "B"],
        "targets": {"A": 10.0, "B": -2.0},
        "tolerances": {"A": 0.5, "B": 0.2},
        "hold_sec": 60.0,
    }
    check.update(overrides)
    return check


def test_frame_value_supports_aliases_and_raw_tm_codes():
    frame = make_frame(
        0.0,
        attitude_angle_deg=[1.0, 2.0, 3.0],
        values={"CUSTOM001": 42.5},
    )

    assert frame_value(frame, "roll") == 1.0
    assert frame_value(frame, "pitch") == 2.0
    assert frame_value(frame, "CUSTOM001") == 42.5


def test_judgment_starts_only_after_at_least_ten_seconds():
    assert not should_start_judgment(make_frame(9.999), 10.0)
    assert should_start_judgment(make_frame(10.0), 10.0)
    with pytest.raises(ValueError, match="at least 10 seconds"):
        should_start_judgment(make_frame(9.0), 9.0)


def test_unified_convergence_accepts_inclusive_per_field_tolerances():
    state = CheckState()
    check = convergence_check()

    for frame in (
        make_frame(0.0, values={"A": 10.5, "B": -2.2}),
        make_frame(60.0, values={"A": 9.5, "B": -1.8}),
    ):
        update_convergence_check(frame, check, state)

    assert state.satisfied


def test_unified_convergence_restarts_hold_after_any_field_leaves_range():
    state = CheckState()
    check = convergence_check()

    for frame in (
        make_frame(0.0, values={"A": 10.0, "B": -2.0}),
        make_frame(30.0, values={"A": 10.6, "B": -2.0}),
        make_frame(60.0, values={"A": 10.0, "B": -2.0}),
        make_frame(90.0, values={"A": 10.0, "B": -2.0}),
    ):
        update_convergence_check(frame, check, state)

    assert not state.satisfied
    assert state.valid_start_time == 60.0


def test_unified_convergence_supports_scalar_target_and_tolerance():
    state = CheckState()
    check = convergence_check(
        fields=["roll", "pitch", "yaw"],
        targets=0.0,
        tolerances=0.5,
        hold_sec=10.0,
    )

    update_convergence_check(
        make_frame(0.0, [0.5, -0.5, 0.0]),
        check,
        state,
    )
    update_convergence_check(
        make_frame(10.0, [0.1, -0.1, 0.0]),
        check,
        state,
    )

    assert state.satisfied


def test_completed_convergence_is_latched():
    state = CheckState()
    check = convergence_check(hold_sec=0.0)
    update_convergence_check(
        make_frame(0.0, values={"A": 10.0, "B": -2.0}),
        check,
        state,
    )
    update_convergence_check(
        make_frame(2.0, values={"A": 99.0, "B": 99.0}),
        check,
        state,
    )

    assert state.satisfied


def test_control_mode_has_its_own_entry_rule_and_remains_monitored():
    check = {
        "type": "mode",
        "mode": "control_mode",
        "name": "control mode",
        "field": "control_mode",
        "expected": "3",
        "entry": {"roll_pitch_abs_lt_deg": 6.0},
    }
    state = CheckState()

    update_control_mode_check(make_frame(0.0, [7.0, 0.0, 0.0], "2"), check, state, "case")
    assert not state.started

    update_control_mode_check(make_frame(2.0, [5.0, -5.0, 90.0], "3"), check, state, "case")
    assert state.started and state.satisfied

    with pytest.raises(AssertionError, match="control mode mismatch"):
        update_control_mode_check(
            make_frame(4.0, [8.0, 8.0, 0.0], "2"),
            check,
            state,
            "case",
        )


def test_attitude_reference_is_a_separate_mode_handler():
    check = {
        "type": "mode",
        "mode": "attitude_reference",
        "name": "attitude reference",
        "field": "attitude_reference",
        "expected": "1",
    }
    state = CheckState()

    update_check(
        make_frame(10.0, attitude_reference="1"),
        check,
        state,
        "case",
    )

    assert state.started and state.satisfied


def test_assert_checks_completed_lists_every_incomplete_check():
    checks = [
        {"name": "mode", "type": "mode"},
        {"name": "convergence", "type": "convergence"},
    ]
    states = [CheckState(satisfied=True), CheckState(satisfied=False)]

    with pytest.raises(AssertionError, match="convergence"):
        assert_checks_completed(checks, states, "case")
