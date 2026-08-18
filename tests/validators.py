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


def update_mode_check(
    frame: TelemetryFrame,
    check: dict,
    state: CheckState,
    case_id: str,
) -> None:
    """Apply one declarative entry rule and then continuously verify a mode."""
    field = str(check.get("field", "")).strip()
    if not field:
        raise ValueError("mode check field must be configured")
    if "expected" not in check:
        raise ValueError(f"mode check {field!r} expected value must be configured")

    if not state.started:
        state.started = _mode_start_conditions_met(
            frame=frame,
            start_when=check.get("start_when"),
        )
    if not state.started:
        return

    _assert_mode_value(
        actual=frame_value(frame, field),
        expected=check["expected"],
        case_id=case_id,
        check_name=check.get("name", field),
        timestamp_sec=frame.timestamp_sec,
    )
    state.satisfied = True


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
            f"{case_id} mode check {check_name} mismatch; "
            f"expected_engineering_value={expected}, "
            f"actual_engineering_value={actual}, "
            f"t={timestamp_sec:.3f}s"
        )


def _mode_start_conditions_met(
    frame: TelemetryFrame,
    start_when,
) -> bool:
    """Evaluate a safe, declarative all/any group of entry conditions."""
    if start_when is None:
        return True
    if not isinstance(start_when, dict):
        raise ValueError("mode check start_when must be a mapping")

    match = str(start_when.get("match", "all")).strip().lower()
    if match not in {"all", "any"}:
        raise ValueError("mode check start_when.match must be 'all' or 'any'")

    conditions = start_when.get("conditions", [])
    if not isinstance(conditions, list):
        raise ValueError("mode check start_when.conditions must be a list")
    if not conditions:
        return True

    results = [
        _mode_start_condition_met(frame=frame, condition=condition)
        for condition in conditions
    ]
    return all(results) if match == "all" else any(results)


def _mode_start_condition_met(
    frame: TelemetryFrame,
    condition,
) -> bool:
    if not isinstance(condition, dict):
        raise ValueError("each mode start condition must be a mapping")

    field = str(condition.get("field", "")).strip()
    operator = str(condition.get("operator", "")).strip().lower()
    if not field:
        raise ValueError("mode start condition field must be configured")
    if "value" not in condition:
        raise ValueError(
            f"mode start condition {field!r} value must be configured"
        )

    actual = frame_value(frame, field)
    expected = condition["value"]
    if operator == "eq":
        return str(actual) == str(expected)
    if operator == "ne":
        return str(actual) != str(expected)

    supported_numeric_operators = {
        "lt",
        "le",
        "gt",
        "ge",
        "abs_lt",
        "abs_le",
    }
    if operator not in supported_numeric_operators:
        supported = "eq, ne, lt, le, gt, ge, abs_lt, abs_le"
        raise ValueError(
            f"unsupported mode start condition operator {operator!r}; "
            f"supported operators: {supported}"
        )

    actual_number = float(actual)
    expected_number = float(expected)
    if operator.startswith("abs_") and expected_number < 0:
        raise ValueError("absolute mode start condition value must not be negative")

    comparisons = {
        "lt": actual_number < expected_number,
        "le": actual_number <= expected_number,
        "gt": actual_number > expected_number,
        "ge": actual_number >= expected_number,
        "abs_lt": abs(actual_number) < expected_number,
        "abs_le": abs(actual_number) <= expected_number,
    }
    return comparisons[operator]


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
