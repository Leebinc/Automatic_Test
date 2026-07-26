import json
import struct

from src.models import InitialCondition, TelemetryFrame, TelemetryParameter


INITIAL_CONDITION_ENDIAN = "<"
INITIAL_CONDITION_BODY_FORMAT = INITIAL_CONDITION_ENDIAN + "6dH5B6f6d3f4B"
INITIAL_CONDITION_BODY_SIZE = struct.calcsize(INITIAL_CONDITION_BODY_FORMAT)

ORBIT_FIELD_ORDER = (
    "semi_major_axis_m",
    "eccentricity",
    "inclination_deg",
    "raan_deg",
    "arg_perigee_deg",
    "mean_anomaly_deg",
)

DATE_FIELD_ORDER = (
    "month",
    "date",
    "hour",
    "minute",
    "second",
)

ATTITUDE_FIELD_ORDER = (
    "roll",
    "pitch",
    "yaw",
    "roll_angular_velocity",
    "pitch_angular_velocity",
    "yaw_angular_velocity",
)

SPARE1_FIELD_ORDER = (
    "spare1",
    "spare2",
    "spare3",
    "spare4",
    "spare5",
    "spare6",
)

SPARE2_FIELD_ORDER = (
    "spare7",
    "spare8",
    "spare9",
)

CONFIG_FIELD_ORDER = (
    "Attitude_dynamics_open_closed_loop_control",
    "Orbit_open_closed_loop_control",
    "Reset",
    "Initial_orbit_standard",
)


def calculate_uint16_checksum(data: bytes) -> int:
    """Return the low 16 bits of the unsigned byte sum."""
    return sum(data) & 0xFFFF


def encode_initial_condition(condition: InitialCondition, reset: bool) -> bytes:
    missing_orbit_fields = [
        field for field in ORBIT_FIELD_ORDER if field not in condition.orbit
    ]
    if missing_orbit_fields:
        raise ValueError(f"missing orbit fields: {missing_orbit_fields}")

    if not condition.year:
        raise ValueError("Invalid year input")

    missing_date_fields = [
        field for field in DATE_FIELD_ORDER if field not in condition.date_time
    ]
    if missing_date_fields:
        raise ValueError(f"missing date fields: {missing_date_fields}")

    missing_attitude_fields = [
        field for field in ATTITUDE_FIELD_ORDER if field not in condition.attitude
    ]
    if missing_attitude_fields:
        raise ValueError(f"missing attitude fields: {missing_attitude_fields}")

    missing_spare1_fields = [
        field for field in SPARE1_FIELD_ORDER if field not in condition.Spare1
    ]
    if missing_spare1_fields:
        raise ValueError(f"missing sapre1 fields: {missing_spare1_fields}")

    missing_spare2_fields = [
        field for field in SPARE2_FIELD_ORDER if field not in condition.Spare2
    ]
    if missing_spare2_fields:
        raise ValueError(f"missing sapre2 fields: {missing_spare2_fields}")

    missing_config_fields = [
        field for field in CONFIG_FIELD_ORDER if field not in condition.Config
    ]
    if missing_config_fields:
        raise ValueError(f"missing config fields: {missing_config_fields}")

    values = (
        [float(condition.orbit[field]) for field in ORBIT_FIELD_ORDER]
        + [int(condition.year)]
        + [int(condition.date_time[field]) for field in DATE_FIELD_ORDER]
        + [float(condition.attitude[field]) for field in ATTITUDE_FIELD_ORDER]
        + [float(condition.Spare1[field]) for field in SPARE1_FIELD_ORDER]
        + [float(condition.Spare2[field]) for field in SPARE2_FIELD_ORDER]
        + [int(condition.Config["Attitude_dynamics_open_closed_loop_control"])]
        + [int(condition.Config["Orbit_open_closed_loop_control"])]
        + [int(condition.Config["Reset"][1 if reset else 0])]
        + [int(condition.Config["Initial_orbit_standard"])]
    )

    body = struct.pack(
        INITIAL_CONDITION_BODY_FORMAT,
        *values,
    )
    return body


def encode_tcp_json_command(payload: dict) -> bytes:
    """Encode one TCP JSON command. The server requires one JSON object per line."""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"


def decode_tcp_json_response(line: bytes) -> dict:
    return json.loads(line.decode("utf-8"))


def decode_telemetry_parameter(payload: dict) -> TelemetryParameter:
    return TelemetryParameter(
        tm_code=str(payload.get("tmCode", "")),
        tm_name=str(payload.get("tmName", "")),
        subsystem=str(payload.get("subsystem", "")),
        value=payload.get("value"),
        state=int(payload.get("state", 0)),
        state_message=str(payload.get("stateMessage", "")),
        source=_optional_int(payload.get("source")),
        valid=_optional_int(payload.get("valid")),
        timestamp_ms=_optional_int(payload.get("timestampMs")),
        source_type=str(payload.get("sourceType", "")),
        raw=payload,
    )


def decode_telemetry_response_parameters(response: dict) -> list[TelemetryParameter]:
    code = int(response.get("code", -1))
    if code != 0:
        raise ValueError(
            f"telemetry server returned error code={code}, "
            f"msg={response.get('msg', '')}"
        )

    data = response.get("data", [])
    if isinstance(data, dict):
        data = [data]
    if data is None:
        data = []
    if not isinstance(data, list):
        raise ValueError(f"telemetry response data must be object or array: {data!r}")

    return [
        decode_telemetry_parameter(item)
        for item in data
        if isinstance(item, dict)
    ]


def decode_telemetry_response_frame(
    response: dict,
    field_codes: dict | None = None,
) -> TelemetryFrame:
    """
    Convert the request-response telemetry protocol into the framework's
    TelemetryFrame shape.

    field_codes may contain:
    - case_id: telemetry code whose value is the case id
    - timestamp_sec: telemetry code whose value is seconds
    - timestamp_ms: telemetry code whose value is milliseconds
    - attitude_angle_deg: list of three telemetry codes [roll, pitch, yaw]
    - angular_rate_deg_s: list of three telemetry codes [x, y, z]
    - control_mode: telemetry code whose value is the mode string
    - attitude_reference: telemetry code whose value is the attitude reference
    """
    field_codes = field_codes or {}
    parameters = decode_telemetry_response_parameters(response)
    by_code = {item.tm_code: item for item in parameters}

    def value_for(field: str, default=None):
        tm_code = field_codes.get(field)
        if not tm_code:
            return default
        item = by_code.get(str(tm_code))
        if item is None:
            return default
        return item.value

    timestamp_value = value_for("timestamp_sec")
    if timestamp_value is not None:
        timestamp_sec = float(timestamp_value)
    else:
        timestamp_ms_value = value_for("timestamp_ms")
        if timestamp_ms_value is not None:
            timestamp_sec = float(timestamp_ms_value) / 1000.0
        else:
            timestamps = [
                item.timestamp_ms for item in parameters if item.timestamp_ms is not None
            ]
            timestamp_sec = max(timestamps) / 1000.0 if timestamps else 0.0

    attitude_angle_deg = _three_axis_values(
        by_code=by_code,
        field_codes=field_codes.get("attitude_angle_deg", []),
        axis_names=("roll", "pitch", "yaw"),
    )
    angular_rate_deg_s = _optional_three_axis_values(
        by_code=by_code,
        field_codes=field_codes.get("angular_rate_deg_s", []),
        axis_names=("x", "y", "z"),
    )

    return TelemetryFrame(
        case_id=str(value_for("case_id", "")),
        timestamp_sec=timestamp_sec,
        attitude_angle_deg=attitude_angle_deg,
        angular_rate_deg_s=angular_rate_deg_s,
        control_mode=str(value_for("control_mode", "UNKNOWN")),
        attitude_reference=str(value_for("attitude_reference", "UNKNOWN")),
        sim_status="RUNNING",
        raw=response,
    )


def _three_axis_values(
    by_code: dict[str, TelemetryParameter],
    field_codes,
    axis_names: tuple[str, str, str],
) -> list[float]:
    if isinstance(field_codes, dict):
        field_codes = [field_codes.get(axis) for axis in axis_names]

    configured_codes = list(field_codes or [])[:3]
    if len(configured_codes) != 3 or any(code is None for code in configured_codes):
        raise ValueError(
            f"three telemetry codes must be configured for axes {axis_names}: "
            f"{configured_codes!r}"
        )

    values = []
    for tm_code in configured_codes:
        item = by_code.get(str(tm_code))
        if item is None:
            raise ValueError(f"telemetry response is missing code: {tm_code}")
        values.append(float(item.value))

    return values


def _optional_three_axis_values(
    by_code: dict[str, TelemetryParameter],
    field_codes,
    axis_names: tuple[str, str, str],
) -> list[float] | None:
    if not field_codes:
        return None

    if isinstance(field_codes, dict):
        field_codes = [field_codes.get(axis) for axis in axis_names]

    configured_codes = list(field_codes or [])[:3]
    if len(configured_codes) != 3 or any(code is None for code in configured_codes):
        raise ValueError(
            f"three telemetry codes must be configured for optional axes "
            f"{axis_names}: {configured_codes!r}"
        )

    items = [by_code.get(str(tm_code)) for tm_code in configured_codes]
    present_count = sum(item is not None for item in items)
    if present_count == 0:
        return None
    if present_count != 3:
        missing_codes = [
            tm_code
            for tm_code, item in zip(configured_codes, items)
            if item is None
        ]
        raise ValueError(
            f"telemetry response contains only part of the optional three-axis "
            f"data; missing codes: {missing_codes}"
        )

    return [float(item.value) for item in items]


def _optional_int(value) -> int | None:
    if value is None:
        return None
    return int(value)
