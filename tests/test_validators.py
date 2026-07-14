from src.models import TelemetryFrame
from src.validators import (
    final_attitude_reference_is,
    has_converged_for_hold_time,
    is_angular_rate_below_threshold,
    is_attitude_angle_below_threshold,
    has_attitude_converged_for_hold_time,
)


def make_frame(
    timestamp_sec: float,
    attitude_angle_deg: list[float],
    angular_rate_deg_s: list[float] | None = None,
    attitude_reference: str = "SUN",
) -> TelemetryFrame:
    return TelemetryFrame(
        case_id="unit_case",
        timestamp_sec=timestamp_sec,
        attitude_angle_deg=attitude_angle_deg,
        angular_rate_deg_s=angular_rate_deg_s,
        control_mode="STABLE",
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


def test_final_attitude_reference():
    frames = [
        make_frame(0.0, [0.0, 0.0, 0.0], attitude_reference="EARTH"),
        make_frame(2.0, [0.0, 0.0, 0.0], attitude_reference="SUN"),
    ]

    assert final_attitude_reference_is(frames, expected_reference="SUN")
