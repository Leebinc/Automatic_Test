import pytest

from src.models import TelemetryFrame
from tests.validators import (
    assert_attitude_reference_matches,
    assert_control_mode_matches,
    has_attitude_converged_for_hold_time,
    has_converged_for_hold_time,
    is_angular_rate_below_threshold,
    is_attitude_angle_below_threshold,
    is_roll_and_pitch_below_threshold,
    should_check_attitude_reference,
    should_check_control_mode,
)


def make_frame(
    timestamp_sec: float,
    attitude_angle_deg: list[float],
    angular_rate_deg_s: list[float] | None = None,
    control_mode: str = "3",
    attitude_reference: str = "3",
) -> TelemetryFrame:
    return TelemetryFrame(
        case_id="unit_case",
        timestamp_sec=timestamp_sec,
        attitude_angle_deg=attitude_angle_deg,
        angular_rate_deg_s=angular_rate_deg_s,
        control_mode=control_mode,
        attitude_reference=attitude_reference,
        sim_status="RUNNING",
        raw={},
    )


def test_attitude_angle_threshold_is_inclusive():
    frame = make_frame(0.0, [0.5, -0.5, 0.0])

    assert is_attitude_angle_below_threshold(frame, threshold_deg=0.5)


def test_attitude_angle_must_remain_in_range_for_full_hold_time():
    frames = [
        make_frame(0.0, [0.5, -0.5, 0.1]),
        make_frame(30.0, [0.2, -0.1, 0.0]),
        make_frame(60.0, [0.0, 0.0, 0.0]),
    ]

    assert has_attitude_converged_for_hold_time(
        frames=frames,
        threshold_deg=0.5,
        hold_sec=60.0,
    )


def test_out_of_range_angle_restarts_hold_time():
    frames = [
        make_frame(0.0, [0.0, 0.0, 0.0]),
        make_frame(30.0, [0.6, 0.0, 0.0]),
        make_frame(60.0, [0.0, 0.0, 0.0]),
        make_frame(90.0, [0.0, 0.0, 0.0]),
    ]

    assert not has_attitude_converged_for_hold_time(
        frames=frames,
        threshold_deg=0.5,
        hold_sec=60.0,
    )


def test_angular_rate_interface_is_retained():
    frames = [
        make_frame(
            0.0,
            [10.0, 10.0, 10.0],
            angular_rate_deg_s=[0.05, -0.05, 0.0],
        ),
        make_frame(
            60.0,
            [10.0, 10.0, 10.0],
            angular_rate_deg_s=[0.01, -0.01, 0.0],
        ),
    ]

    assert is_angular_rate_below_threshold(frames[0], threshold_deg_s=0.05)
    assert has_converged_for_hold_time(
        frames,
        threshold_deg_s=0.05,
        hold_sec=60.0,
    )


def test_missing_optional_angular_rate_does_not_converge():
    frame = make_frame(0.0, [0.0, 0.0, 0.0], angular_rate_deg_s=None)

    assert not is_angular_rate_below_threshold(frame, threshold_deg_s=0.05)


def test_control_mode_check_trigger_uses_roll_and_pitch_strictly_below_limit():
    assert is_roll_and_pitch_below_threshold(
        make_frame(0.0, [5.999, -5.999, 100.0]),
        threshold_deg=6.0,
    )
    assert not is_roll_and_pitch_below_threshold(
        make_frame(0.0, [6.0, 0.0, 0.0]),
        threshold_deg=6.0,
    )
    assert not is_roll_and_pitch_below_threshold(
        make_frame(0.0, [0.0, -6.0, 0.0]),
        threshold_deg=6.0,
    )


def test_control_mode_check_remains_started_after_angle_leaves_range():
    check_started = False
    states = []
    for frame in (
        make_frame(0.0, [7.0, 0.0, 0.0]),
        make_frame(2.0, [5.0, -5.0, 90.0]),
        make_frame(4.0, [8.0, 8.0, 0.0]),
    ):
        check_started = should_check_control_mode(
            frame=frame,
            threshold_deg=6.0,
            check_started=check_started,
        )
        states.append(check_started)

    assert states == [False, True, True]


def test_attitude_reference_check_starts_after_configured_delay():
    assert not should_check_attitude_reference(
        make_frame(9.999, [0.0, 0.0, 0.0]),
        10.0,
    )
    assert should_check_attitude_reference(
        make_frame(10.0, [0.0, 0.0, 0.0]),
        10.0,
    )


def test_control_mode_mismatch_at_any_frame_raises_immediately():
    frames = [
        make_frame(0.0, [0.0, 0.0, 0.0], control_mode="3"),
        make_frame(2.0, [0.0, 0.0, 0.0], control_mode="2"),
        make_frame(4.0, [0.0, 0.0, 0.0], control_mode="3"),
    ]

    with pytest.raises(AssertionError, match="control mode mismatch"):
        for frame in frames:
            assert_control_mode_matches(frame, expected_mode="3", case_id="case")


def test_attitude_reference_mismatch_at_any_frame_raises_immediately():
    frames = [
        make_frame(0.0, [0.0, 0.0, 0.0], attitude_reference="3"),
        make_frame(2.0, [0.0, 0.0, 0.0], attitude_reference="1"),
        make_frame(4.0, [0.0, 0.0, 0.0], attitude_reference="3"),
    ]

    with pytest.raises(AssertionError, match="attitude reference mismatch"):
        for frame in frames:
            assert_attitude_reference_matches(
                frame,
                expected_reference="3",
                case_id="case",
            )
