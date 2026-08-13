import math
from dataclasses import dataclass

from src.models import TelemetryFrame


@dataclass
class CheckState:
    started: bool = False
    satisfied: bool = False
    valid_start_time: float | None = None


def should_start_judgment(frame: TelemetryFrame, delay_sec: float) -> bool:
    if delay_sec < 10.0:
        raise ValueError("judgment delay must be at least 10 seconds")
    return frame.timestamp_sec >= delay_sec


def frame_value(frame: TelemetryFrame, field: str):
    """Read a named frame field or a raw engineering value by tmCode."""
    if field in frame.values:
        return frame.values[field]

    aliases = {
        "roll": lambda: frame.attitude_angle_deg[0],
        "pitch": lambda: frame.attitude_angle_deg[1],
        "yaw": lambda: frame.attitude_angle_deg[2],
        "rate_x": lambda: frame.angular_rate_deg_s[0],
        "rate_y": lambda: frame.angular_rate_deg_s[1],
        "rate_z": lambda: frame.angular_rate_deg_s[2],
        "control_mode": lambda: frame.control_mode,
        "attitude_reference": lambda: frame.attitude_reference,
    }
    if field in aliases:
        try:
            return aliases[field]()
        except (IndexError, TypeError):
            raise ValueError(f"telemetry frame has no value for field: {field}")
    raise ValueError(f"telemetry frame has no value for field/tmCode: {field}")


def update_control_mode_check(
    frame: TelemetryFrame,
    check: dict,
    state: CheckState,
    case_id: str,
) -> None:
    """Control-mode entry rule: roll and pitch must enter the configured range."""
    entry = check.get("entry", {})
    threshold = float(entry["roll_pitch_abs_lt_deg"])
    if not state.started:
        roll = float(frame_value(frame, "roll"))
        pitch = float(frame_value(frame, "pitch"))
        state.started = abs(roll) < threshold and abs(pitch) < threshold

    if not state.started:
        return

    _assert_mode_value(
        actual=frame_value(frame, check.get("field", "control_mode")),
        expected=check["expected"],
        case_id=case_id,
        check_name=check.get("name", "control_mode"),
        timestamp_sec=frame.timestamp_sec,
    )
    state.satisfied = True


def update_attitude_reference_check(
    frame: TelemetryFrame,
    check: dict,
    state: CheckState,
    case_id: str,
) -> None:
    """Attitude-reference rule: start on the first frame after the global delay."""
    state.started = True
    _assert_mode_value(
        actual=frame_value(frame, check.get("field", "attitude_reference")),
        expected=check["expected"],
        case_id=case_id,
        check_name=check.get("name", "attitude_reference"),
        timestamp_sec=frame.timestamp_sec,
    )
    state.satisfied = True


MODE_CHECK_HANDLERS = {
    "control_mode": update_control_mode_check,
    "attitude_reference": update_attitude_reference_check,
}


def update_mode_check(
    frame: TelemetryFrame,
    check: dict,
    state: CheckState,
    case_id: str,
) -> None:
    mode = str(check.get("mode", ""))
    try:
        handler = MODE_CHECK_HANDLERS[mode]
    except KeyError as exc:
        raise ValueError(f"unsupported mode check: {mode!r}") from exc
    handler(frame=frame, check=check, state=state, case_id=case_id)


def update_convergence_check(
    frame: TelemetryFrame,
    check: dict,
    state: CheckState,
) -> None:
    """Apply one shared target/tolerance/hold-time rule to any telemetry fields."""
    if state.satisfied:
        return

    fields = check.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("convergence check fields must be a non-empty list")

    targets = _expand_per_field(check.get("targets", 0.0), fields, "targets")
    tolerances = _expand_per_field(check.get("tolerances"), fields, "tolerances")
    hold_sec = float(check.get("hold_sec", 0.0))
    if hold_sec < 0:
        raise ValueError("convergence hold_sec must not be negative")

    in_range = all(
        _is_within_tolerance(
            actual=float(frame_value(frame, field)),
            target=float(target),
            tolerance=float(tolerance),
        )
        for field, target, tolerance in zip(fields, targets, tolerances)
    )
    state.started = True
    if not in_range:
        state.valid_start_time = None
        state.satisfied = False
        return

    if state.valid_start_time is None:
        state.valid_start_time = frame.timestamp_sec
    state.satisfied = (
        frame.timestamp_sec - state.valid_start_time >= hold_sec
    )


def update_check(
    frame: TelemetryFrame,
    check: dict,
    state: CheckState,
    case_id: str,
) -> None:
    check_type = str(check.get("type", ""))
    if check_type == "mode":
        update_mode_check(frame, check, state, case_id)
        return
    if check_type == "convergence":
        update_convergence_check(frame, check, state)
        return
    raise ValueError(f"unsupported check type: {check_type!r}")


def assert_checks_completed(
    checks: list[dict],
    states: list[CheckState],
    case_id: str,
) -> None:
    incomplete = [
        check.get("name", f"check_{index + 1}")
        for index, (check, state) in enumerate(zip(checks, states))
        if not state.satisfied
    ]
    if incomplete:
        raise AssertionError(
            f"{case_id} checks did not complete: {', '.join(incomplete)}"
        )


def _assert_mode_value(
    actual,
    expected,
    case_id: str,
    check_name: str,
    timestamp_sec: float,
) -> None:
    if str(actual) != str(expected):
        raise AssertionError(
            f"{case_id} {check_name} mismatch; "
            f"expected_engineering_value={expected}, "
            f"actual_engineering_value={actual}, "
            f"t={timestamp_sec:.3f}s"
        )


def _is_within_tolerance(actual: float, target: float, tolerance: float) -> bool:
    if tolerance < 0:
        raise ValueError("convergence tolerance must not be negative")
    difference = abs(actual - target)
    return difference <= tolerance or math.isclose(
        difference,
        tolerance,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )


def _expand_per_field(value, fields: list[str], label: str) -> list:
    if isinstance(value, dict):
        missing = [field for field in fields if field not in value]
        if missing:
            raise ValueError(f"{label} missing fields: {missing}")
        return [value[field] for field in fields]
    if isinstance(value, (list, tuple)):
        if len(value) != len(fields):
            raise ValueError(
                f"{label} length must match fields: {len(value)} != {len(fields)}"
            )
        return list(value)
    if value is None:
        raise ValueError(f"{label} must be configured")
    return [value] * len(fields)
