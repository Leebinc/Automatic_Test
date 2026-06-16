import time

import yaml

from src.models import ExpectedResult, InitialCondition, SimulationCase
from src.tcp_telemetry_client import TcpTelemetryClient
from src.udp_client import UdpInitialConditionClient


def load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_cases(path: str) -> list[SimulationCase]:
    raw_cases = load_yaml(path)
    cases = []

    for item in raw_cases:
        cases.append(
            SimulationCase(
                case_id=item["case_id"],
                description=item["description"],
                initial_condition=InitialCondition(**item["initial_condition"]),
                expected=ExpectedResult(**item["expected"]),
            )
        )

    return cases


class SimulationRunner:
    """
    Run one hardware-in-the-loop simulation case.

    Flow for two-machine testing:
    1. Send initial condition to the simulator machine by UDP.
    2. Wait briefly so the simulator can prepare telemetry output.
    3. Connect to the simulator telemetry server by TCP.
    4. Receive telemetry frames until FINISHED or the max duration is reached.
    """

    def __init__(self, env_config: dict):
        udp_config = env_config["udp"]
        tcp_config = env_config["tcp"]
        simulation_config = env_config.get("simulation", {})

        self.udp_client = UdpInitialConditionClient(
            host=udp_config["host"],
            port=int(udp_config["port"]),
            timeout_sec=float(udp_config.get("timeout_sec", 2.0)),
        )
        self.tcp_client = TcpTelemetryClient(
            host=tcp_config["host"],
            port=int(tcp_config["port"]),
            timeout_sec=float(tcp_config.get("timeout_sec", 2.0)),
            recv_buffer_size=int(tcp_config.get("recv_buffer_size", 4096)),
            connect_retry_timeout_sec=float(
                tcp_config.get("connect_retry_timeout_sec", 20.0)
            ),
            connect_retry_interval_sec=float(
                tcp_config.get("connect_retry_interval_sec", 0.5)
            ),
            telemetry_poll_codes=tcp_config.get("telemetry_poll_codes"),
            telemetry_frame_field_codes=tcp_config.get("telemetry_frame_field_codes"),
            poll_interval_sec=float(tcp_config.get("poll_interval_sec", 0.1)),
        )

        self.max_duration_sec = float(
            simulation_config.get("max_duration_sec", 120.0)
        )
        self.post_udp_delay_sec = float(
            simulation_config.get("post_udp_delay_sec", 0.2)
        )
        self.reset_sequence_delay_sec = float(
            simulation_config.get("reset_sequence_delay_sec", 3.0)
        )

    def run_once(self, sim_case: SimulationCase):
        reset_clear_payload = self.udp_client.send_initial_condition(
            sim_case.initial_condition,
            reset=False,
        )
        print(
            f"sent initial condition by UDP: "
            f"case_id={sim_case.case_id}, reset=0, "
            f"bytes={len(reset_clear_payload)}"
        )

        if self.reset_sequence_delay_sec > 0:
            time.sleep(self.reset_sequence_delay_sec)

        reset_start_payload = self.udp_client.send_initial_condition(
            sim_case.initial_condition,
            reset=True,
        )
        print(
            f"sent initial condition by UDP: "
            f"case_id={sim_case.case_id}, reset=1, "
            f"bytes={len(reset_start_payload)}"
        )

        if self.post_udp_delay_sec > 0:
            time.sleep(self.post_udp_delay_sec)

        frames = []
        start_time = time.monotonic()

        for frame in self._receive_frames():
            frames.append(frame)

            if frame.sim_status == "FINISHED":
                break

            if time.monotonic() - start_time >= self.max_duration_sec:
                print(
                    f"stop receiving telemetry because max_duration_sec "
                    f"was reached: {self.max_duration_sec}s"
                )
                break

        print(f"received telemetry frames: {len(frames)}")
        return frames

    def _receive_frames(self):
        try:
            yield from self.tcp_client.receive_frames(
                max_duration_sec=self.max_duration_sec
            )
        except TypeError:
            yield from self.tcp_client.receive_frames()
