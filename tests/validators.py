from src.models import TelemetryFrame


def is_angular_rate_below_threshold(
    frame: TelemetryFrame,
    threshold_deg_s: float,
) -> bool:
    if frame.angular_rate_deg_s is None or len(frame.angular_rate_deg_s) != 3:
        return False

    wx, wy, wz = frame.angular_rate_deg_s
    return (
        abs(wx) <= threshold_deg_s
        and abs(wy) <= threshold_deg_s
        and abs(wz) <= threshold_deg_s
    )


def is_attitude_angle_below_threshold(
    frame: TelemetryFrame,
    threshold_deg: float,
) -> bool:
    if len(frame.attitude_angle_deg) != 3:
        return False

    roll, pitch, yaw = frame.attitude_angle_deg
    return (
        abs(roll) <= threshold_deg
        and abs(pitch) <= threshold_deg
        and abs(yaw) <= threshold_deg
    )


def is_roll_and_pitch_below_threshold(
    frame: TelemetryFrame,
    threshold_deg: float,
) -> bool:
    """Return whether roll and pitch are both strictly inside the threshold."""
    if len(frame.attitude_angle_deg) != 3:
        return False

    roll, pitch, _ = frame.attitude_angle_deg
    return abs(roll) < threshold_deg and abs(pitch) < threshold_deg


def should_check_control_mode(
    frame: TelemetryFrame,
    threshold_deg: float,
    check_started: bool,
) -> bool:
    """Latch control-mode judgment after roll and pitch first enter range."""
    return check_started or is_roll_and_pitch_below_threshold(
        frame=frame,
        threshold_deg=threshold_deg,
    )


def has_angular_rate_converged_for_hold_time(
    frames: list[TelemetryFrame],
    threshold_deg_s: float,
    hold_sec: float,
) -> bool:
    if not frames:
        return False

    valid_start_time = None
    for frame in frames:
        if is_angular_rate_below_threshold(frame, threshold_deg_s):
            if valid_start_time is None:
                valid_start_time = frame.timestamp_sec
            if frame.timestamp_sec - valid_start_time >= hold_sec:
                return True
        else:
            valid_start_time = None

    return False


def has_converged_for_hold_time(
    frames: list[TelemetryFrame],
    threshold_deg_s: float,
    hold_sec: float,
) -> bool:
    """Backward-compatible angular-rate convergence interface."""
    return has_angular_rate_converged_for_hold_time(
        frames=frames,
        threshold_deg_s=threshold_deg_s,
        hold_sec=hold_sec,
    )


def has_attitude_converged_for_hold_time(
    frames: list[TelemetryFrame],
    threshold_deg: float,
    hold_sec: float,
) -> bool:
    if not frames:
        return False

    valid_start_time = None
    for frame in frames:
        if is_attitude_angle_below_threshold(frame, threshold_deg):
            if valid_start_time is None:
                valid_start_time = frame.timestamp_sec
            if frame.timestamp_sec - valid_start_time >= hold_sec:
                return True
        else:
            valid_start_time = None

    return False


def should_check_attitude_reference(
    frame: TelemetryFrame,
    delay_sec: float,
) -> bool:
    """Return whether attitude-reference judgment should start."""
    return frame.timestamp_sec >= max(float(delay_sec), 0.0)


def assert_control_mode_matches(
    frame: TelemetryFrame,
    expected_mode,
    case_id: str,
) -> None:
    expected_engineering_value = str(expected_mode)
    actual_engineering_value = str(frame.control_mode)
    if actual_engineering_value != expected_engineering_value:
        raise AssertionError(
            f"{case_id} control mode mismatch; "
            f"expected_engineering_value={expected_engineering_value}, "
            f"actual_engineering_value={actual_engineering_value}, "
            f"t={frame.timestamp_sec:.3f}s"
        )


def assert_attitude_reference_matches(
    frame: TelemetryFrame,
    expected_reference,
    case_id: str,
) -> None:
    expected_engineering_value = str(expected_reference)
    actual_engineering_value = str(frame.attitude_reference)
    if actual_engineering_value != expected_engineering_value:
        raise AssertionError(
            f"{case_id} attitude reference mismatch; "
            f"expected_engineering_value={expected_engineering_value}, "
            f"actual_engineering_value={actual_engineering_value}, "
            f"t={frame.timestamp_sec:.3f}s"
        )
