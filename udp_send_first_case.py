from src.runner import load_cases, load_yaml
from src.protocols import INITIAL_CONDITION_BODY_SIZE
from src.udp_client import UdpInitialConditionClient
import time


ENV_CONFIG_PATH = "config/env.yaml"
CASES_PATH = "config/cases.yaml"


def send_first_case_initial_condition(reset: bool) -> bytes:
    env_config = load_yaml(ENV_CONFIG_PATH)
    first_case = load_cases(CASES_PATH)[0]
    udp_config = env_config["udp"]

    client = UdpInitialConditionClient(
        host=udp_config["host"],
        port=int(udp_config["port"]),
        timeout_sec=float(udp_config.get("timeout_sec", 2.0)),
    )

    payload = client.send_initial_condition(first_case.initial_condition, reset)

    print(f"UDP target: {udp_config['host']}:{udp_config['port']}")
    print(f"case_id: {first_case.case_id}")
    print(f"reset: {int(reset)}")
    print(f"binary_payload_size: {len(payload)} bytes")
    print(f"expected_payload_size: {INITIAL_CONDITION_BODY_SIZE} bytes")
    print(f"binary_payload_hex: {payload.hex(' ')}")

    return payload


if __name__ == "__main__":
    send_first_case_initial_condition(False)
    time.sleep(3)
    send_first_case_initial_condition(True)
