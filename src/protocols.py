import json
import struct

from src.models import InitialCondition, TelemetryFrame, TelemetryParameter


INITIAL_CONDITION_MAGIC = b"SIC1"
INITIAL_CONDITION_VERSION = 1
INITIAL_CONDITION_ENDIAN = "<"
INITIAL_CONDITION_BODY_FORMAT = INITIAL_CONDITION_ENDIAN + "4sBBH13d"
INITIAL_CONDITION_CHECKSUM_FORMAT = INITIAL_CONDITION_ENDIAN + "H"
INITIAL_CONDITION_FORMAT = (
    INITIAL_CONDITION_BODY_FORMAT + INITIAL_CONDITION_CHECKSUM_FORMAT[1:]
)
INITIAL_CONDITION_BODY_SIZE = struct.calcsize(INITIAL_CONDITION_BODY_FORMAT)
INITIAL_CONDITION_CHECKSUM_SIZE = struct.calcsize(INITIAL_CONDITION_CHECKSUM_FORMAT)
INITIAL_CONDITION_SIZE = struct.calcsize(INITIAL_CONDITION_FORMAT)

CONTROL_MODE_CODES = {
    "UNKNOWN": 0,
    "DETUMBLE": 1,
    "STABLE": 2,
    "SUN_POINTING": 3,
    "EARTH_POINTING": 4,
    "SAFE": 5,
}

ORBIT_FIELD_ORDER = (
    "semi_major_axis_m",
    "eccentricity",
    "inclination_deg",
    "raan_deg",
    "arg_perigee_deg",
    "true_anomaly_deg",
)


def calculate_uint16_checksum(data: bytes) -> int:
    """Return the low 16 bits of the unsigned byte sum."""
    return sum(data) & 0xFFFF


def encode_initial_condition(condition: InitialCondition) -> bytes:
    """
    Encode one simulation initial condition as a fixed-length binary UDP frame.

    Binary layout, little-endian:
    - magic: 4 bytes, ASCII "SIC1"
    - version: uint8, currently 1
    - control_mode: uint8, see CONTROL_MODE_CODES
    - reserved: uint16, currently 0
    - attitude_quat: 4 float64, [q0, q1, q2, q3]
    - orbit: 6 float64, ordered by ORBIT_FIELD_ORDER
    - angular_rate_deg_s: 3 float64, [wx, wy, wz]
    - checksum: uint16, byte-sum of all previous frame bytes
    """
    if len(condition.attitude_quat) != 4:
        raise ValueError("attitude_quat must contain 4 values")

    if len(condition.angular_rate_deg_s) != 3:
        raise ValueError("angular_rate_deg_s must contain 3 values")

    missing_orbit_fields = [
        field for field in ORBIT_FIELD_ORDER if field not in condition.orbit
    ]
    if missing_orbit_fields:
        raise ValueError(f"missing orbit fields: {missing_orbit_fields}")

    control_mode = condition.initial_control_mode.upper()
    control_mode_code = CONTROL_MODE_CODES.get(control_mode)
    if control_mode_code is None:
        raise ValueError(f"unsupported control mode: {condition.initial_control_mode}")

    values = (
        [float(value) for value in condition.attitude_quat]
        + [float(condition.orbit[field]) for field in ORBIT_FIELD_ORDER]
        + [float(value) for value in condition.angular_rate_deg_s]
    )

    body = struct.pack(
        INITIAL_CONDITION_BODY_FORMAT,
        INITIAL_CONDITION_MAGIC,
        INITIAL_CONDITION_VERSION,
        control_mode_code,
        0,
        *values,
    )
    checksum = calculate_uint16_checksum(body)
    return body + struct.pack(INITIAL_CONDITION_CHECKSUM_FORMAT, checksum)


def decode_telemetry_frame(line: bytes) -> TelemetryFrame:
    payload = json.loads(line.decode("utf-8"))

    if "code" in payload and "data" in payload:
        return decode_telemetry_response_frame(payload)

    return TelemetryFrame(
        case_id=str(payload.get("case_id", "")),
        timestamp_sec=float(payload["timestamp_sec"]),
        angular_rate_deg_s=[
            float(payload["angular_rate_deg_s"][0]),
            float(payload["angular_rate_deg_s"][1]),
            float(payload["angular_rate_deg_s"][2]),
        ],
        control_mode=str(payload["control_mode"]),
        sim_status=str(payload.get("sim_status", "RUNNING")),
        raw=payload,
    )


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
    - angular_rate_deg_s: list of three telemetry codes [x, y, z]
    - control_mode: telemetry code whose value is the mode string
    - sim_status: telemetry code whose value is RUNNING/FINISHED
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

    angular_rate_codes = field_codes.get("angular_rate_deg_s", [])
    if isinstance(angular_rate_codes, dict):
        angular_rate_codes = [
            angular_rate_codes.get("x"),
            angular_rate_codes.get("y"),
            angular_rate_codes.get("z"),
        ]
    angular_rate_deg_s = []
    for tm_code in list(angular_rate_codes or [])[:3]:
        item = by_code.get(str(tm_code))
        angular_rate_deg_s.append(float(item.value) if item is not None else 0.0)
    while len(angular_rate_deg_s) < 3:
        angular_rate_deg_s.append(0.0)

    return TelemetryFrame(
        case_id=str(value_for("case_id", "")),
        timestamp_sec=timestamp_sec,
        angular_rate_deg_s=angular_rate_deg_s,
        control_mode=str(value_for("control_mode", "UNKNOWN")),
        sim_status=str(value_for("sim_status", "RUNNING")),
        raw=response,
    )


def _optional_int(value) -> int | None:
    if value is None:
        return None
    return int(value)
