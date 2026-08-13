from src.protocols import decode_telemetry_response_frame


FIELD_CODES = {
    "attitude_angle_deg": ["TMZK0014", "TMZK0016", "TMZK0018"],
    "angular_rate_deg_s": [],
    "control_mode": "TMZK0013_b7b4",
    "attitude_reference": "TMZK0013_b3b0",
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
        parameter("TMZK0014", 0.1),
        parameter("TMZK0016", -0.2),
        parameter("TMZK0018", 0.3),
        parameter("TMZK0013_b7b4", 3),
        parameter("TMZK0013_b3b0", 1),
    ]


def test_frame_can_be_decoded_without_optional_angular_rate():
    frame = decode_telemetry_response_frame(
        response=response_with(required_parameters()),
        field_codes=FIELD_CODES,
    )

    assert frame.attitude_angle_deg == [0.1, -0.2, 0.3]
    assert frame.angular_rate_deg_s is None
    assert frame.control_mode == "3"
    assert frame.attitude_reference == "1"
    assert frame.values["TMZK0014"] == 0.1
    assert frame.values["TMZK0013_b7b4"] == 3
    assert frame.values["roll"] == 0.1
    assert frame.values["pitch"] == -0.2
    assert frame.values["yaw"] == 0.3


def test_frame_still_decodes_complete_optional_angular_rate():
    items = required_parameters() + [
        parameter("WX", 0.01),
        parameter("WY", -0.02),
        parameter("WZ", 0.03),
    ]

    field_codes = dict(FIELD_CODES)
    field_codes["angular_rate_deg_s"] = ["WX", "WY", "WZ"]
    frame = decode_telemetry_response_frame(
        response=response_with(items),
        field_codes=field_codes,
    )

    assert frame.angular_rate_deg_s == [0.01, -0.02, 0.03]


def test_frame_decodes_generic_values_without_attitude_angles():
    field_codes = {
        "attitude_angle_deg": [],
        "angular_rate_deg_s": [],
        "control_mode": "MODE",
    }
    frame = decode_telemetry_response_frame(
        response=response_with(
            [parameter("VOLTAGE", 28.1), parameter("MODE", 3)]
        ),
        field_codes=field_codes,
    )

    assert frame.attitude_angle_deg == []
    assert frame.values == {"VOLTAGE": 28.1, "MODE": 3}


def test_frame_allows_partial_attitude_response_for_per_case_checks():
    frame = decode_telemetry_response_frame(
        response=response_with([parameter("TMZK0014", 0.4)]),
        field_codes=FIELD_CODES,
    )

    assert frame.attitude_angle_deg == []
    assert frame.values["TMZK0014"] == 0.4
    assert frame.values["roll"] == 0.4
