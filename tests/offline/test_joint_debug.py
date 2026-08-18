import json

from src.runner import load_yaml
from tcp_telemetry_joint_debug import parse_interactive_request


def test_interactive_telemetry_commands():
    assert parse_interactive_request("ping") == {"cmd": "ping"}
    assert parse_interactive_request("get ROLL") == {
        "cmd": "get",
        "tmCode": "ROLL",
    }
    assert parse_interactive_request("list ROLL,PITCH,YAW") == {
        "cmd": "list",
        "tmCodes": ["ROLL", "PITCH", "YAW"],
    }


def test_each_case_contains_eight_telecommand_codes_without_source():
    cases = load_yaml("config/cases.yaml")
    expected_codes = [
        "K3072",
        "TEMP031",
        "K3006",
        "ZS300",
        "K3139",
        "K3117",
        "TEMP041",
        "K3036",
    ]

    for case in cases:
        assert "expected" not in case
        assert isinstance(case["checks"], list) and case["checks"]
        configured = case["telecommand"]["command_codes"]
        actual_codes = [
            item if isinstance(item, str) else item["command_code"]
            for item in configured
        ]
        assert actual_codes == expected_codes

    configuration_source = json.dumps(
        {
            "environment": load_yaml("config/env.yaml"),
            "cases": cases,
        }
    )
    assert '"hex"' not in configuration_source
