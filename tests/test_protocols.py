from src.protocols import decode_telemetry_response_frame


FIELD_CODES = {
    "attitude_angle_deg": ["ROLL", "PITCH", "YAW"],
    "angular_rate_deg_s": ["WX", "WY", "WZ"],
    "control_mode": "MODE",
    "attitude_reference": "ATT_REF",
}


def parameter(tm_code: str, value):
    return {
        "tmCode": tm_code,
        "value": value,
        "state": 0,
        "timestampMs": 2000,
    }


def response_with(items: list[dict]) -> dict:
    return {"code": 0, "msg": "success", "data": items}


def required_parameters() -> list[dict]:
    return [
        parameter("ROLL", 0.1),
        parameter("PITCH", -0.2),
        parameter("YAW", 0.3),
        parameter("MODE", "STABLE"),
        parameter("ATT_REF", "SUN"),
    ]


def test_frame_can_be_decoded_without_optional_angular_rate():
    frame = decode_telemetry_response_frame(
        response=response_with(required_parameters()),
        field_codes=FIELD_CODES,
    )

    assert frame.attitude_angle_deg == [0.1, -0.2, 0.3]
    assert frame.angular_rate_deg_s is None
    assert frame.control_mode == "STABLE"
    assert frame.attitude_reference == "SUN"


def test_frame_still_decodes_complete_optional_angular_rate():
    items = required_parameters() + [
        parameter("WX", 0.01),
        parameter("WY", -0.02),
        parameter("WZ", 0.03),
    ]

    frame = decode_telemetry_response_frame(
        response=response_with(items),
        field_codes=FIELD_CODES,
    )

    assert frame.angular_rate_deg_s == [0.01, -0.02, 0.03]
