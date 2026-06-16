from src.models import TelemetryFrame


def is_angular_rate_below_threshold(
    frame: TelemetryFrame,
    threshold_deg_s: float,
) -> bool:
    wx, wy, wz = frame.angular_rate_deg_s
    return (
        abs(wx) <= threshold_deg_s
        and abs(wy) <= threshold_deg_s
        and abs(wz) <= threshold_deg_s
    )


def has_converged_for_hold_time(
    frames: list[TelemetryFrame],
    threshold_deg_s: float,
    hold_sec: float,
) -> bool:
    """
    判断三轴角速度是否连续 hold_sec 时间保持在阈值内。
    """
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


def final_control_mode_is(
    frames: list[TelemetryFrame],
    expected_mode: str,
) -> bool:
    if not frames:
        return False

    return frames[-1].control_mode == expected_mode