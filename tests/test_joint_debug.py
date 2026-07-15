from src.runner import load_yaml
from tcp_telemetry_joint_debug import (
    parse_interactive_request,
    prepare_telecommand_sequence,
)


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


def test_yaml_contains_eight_valid_telecommand_sources():
    env_config = load_yaml("config/env.yaml")

    prepared = prepare_telecommand_sequence(env_config["telecommand"])

    assert len(prepared) == 8
    assert [name for name, _ in prepared] == [
        "K3072_battery_array_test",
        "TEMP031_wheel_speed_clear",
        "K3006_initial_state_soft_reset",
        "ZS300_time_sync",
        "K3139_actuator_output",
        "K3117_allow_ground_hw_single_machine",
        "TEMP041_receive_dynamics_gps",
        "K3036_sun_acquisition_control",
    ]
    assert all(payload for _, payload in prepared)
