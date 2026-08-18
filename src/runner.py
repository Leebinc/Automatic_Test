import time
from threading import Event

import yaml

from src.models import InitialCondition, SimulationCase
from src.tcp_telemetry_client import TcpTelemetryClient
from src.udp_client import UdpInitialConditionClient


def load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_cases(path: str) -> list[SimulationCase]:
    raw_cases = load_yaml(path)
    cases = []

    for item in raw_cases:
        checks = item.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError(
                f"{item.get('case_id', '<unknown>')} checks must be a non-empty list"
            )
        cases.append(
            SimulationCase(
                case_id=item["case_id"],
                description=item["description"],
                initial_condition=InitialCondition(**item["initial_condition"]),
                checks=checks,
                telecommand=item.get("telecommand"),
                telemetry_codes=item.get("telemetry_codes"),
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
        self.telecommand_config = env_config.get("telecommand", {})

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
            heartbeat_interval_sec=float(
                tcp_config.get("heartbeat_interval_sec", 40.0)
            ),
        )
        self.telecommand_client = TcpTelemetryClient(
            host=self.telecommand_config.get("host", tcp_config["host"]),
            port=int(self.telecommand_config.get("port", tcp_config["port"])),
            timeout_sec=float(
                self.telecommand_config.get(
                    "timeout_sec",
                    tcp_config.get("timeout_sec", 2.0),
                )
            ),
            recv_buffer_size=int(
                self.telecommand_config.get(
                    "recv_buffer_size",
                    tcp_config.get("recv_buffer_size", 4096),
                )
            ),
            connect_retry_timeout_sec=float(
                self.telecommand_config.get(
                    "connect_retry_timeout_sec",
                    tcp_config.get("connect_retry_timeout_sec", 20.0),
                )
            ),
            connect_retry_interval_sec=float(
                self.telecommand_config.get(
                    "connect_retry_interval_sec",
                    tcp_config.get("connect_retry_interval_sec", 0.5),
                )
            ),
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
        self.case_interval_sec = float(
            simulation_config.get("case_interval_sec", 0.0)
        )
        self._last_case_end_time = None

    def iter_frames(
        self,
        sim_case: SimulationCase,
        stop_event: Event | None = None,
    ):
        """Prepare one case and yield telemetry frames without judging them."""
        self._wait_for_case_interval()
        try:
            reset_clear_payload = self.udp_client.send_initial_condition(
                sim_case.initial_condition,
                reset=False,
            )
            print(
                f"sent initial condition by UDP: "
                f"case_id={sim_case.case_id}, reset=0, "
                f"bytes={len(reset_clear_payload)}"
            )

            reset_start_sent = self._send_telecommand_if_configured(sim_case)
            if not reset_start_sent:
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

            yield from self._receive_frames(sim_case, stop_event=stop_event)
        finally:
            self._last_case_end_time = time.monotonic()

    def _wait_for_case_interval(self) -> None:
        if self._last_case_end_time is None or self.case_interval_sec <= 0:
            return

        elapsed_sec = time.monotonic() - self._last_case_end_time
        remaining_sec = self.case_interval_sec - elapsed_sec
        if remaining_sec <= 0:
            return

        print(f"waiting {remaining_sec:.3f}s before the next test case")
        time.sleep(remaining_sec)

    def run_once(
        self,
        sim_case: SimulationCase,
        stop_event: Event | None = None,
    ):
        """Compatibility helper that collects frames without judging them."""
        frames = list(self.iter_frames(sim_case, stop_event=stop_event))
        print(f"received telemetry frames: {len(frames)}")
        return frames

    def _send_telecommand_if_configured(self, sim_case: SimulationCase) -> bool:
        telecommand_config = self._resolve_telecommand_config(sim_case)
        command_codes = telecommand_config.get("command_codes")
        if command_codes is None:
            command_code = telecommand_config.get("command_code")
            command_codes = [command_code] if command_code else []

        enabled = _bool_config(
            telecommand_config.get(
                "enabled",
                bool(command_codes),
            )
        )

        if not enabled:
            return False

        if not isinstance(command_codes, list) or not command_codes:
            raise ValueError(
                f"{sim_case.case_id} telecommand.command_codes must be a "
                f"non-empty list"
            )

        return self._send_async_telecommands(
            sim_case=sim_case,
            telecommand_config=telecommand_config,
            command_codes=command_codes,
        )

    def _send_async_telecommands(
        self,
        sim_case: SimulationCase,
        telecommand_config: dict,
        command_codes: list,
    ) -> bool:
        interval_sec = float(telecommand_config.get("interval_sec", 0.1))
        notification_timeout_sec = float(
            telecommand_config.get("notification_timeout_sec", 30.0)
        )
        execution_timeout_sec = float(
            telecommand_config.get("execution_timeout_sec", 60.0)
        )
        reset_start_sent = False

        initial_delay_sec = float(telecommand_config.get("initial_delay_sec", 0.0))
        if initial_delay_sec > 0:
            time.sleep(initial_delay_sec)

        for index, raw_item in enumerate(command_codes):
            if index > 0 and interval_sec > 0:
                time.sleep(interval_sec)

            item = self._normalize_telecommand_item(raw_item, index=index)
            command_code = item["command_code"]
            satellite = item.get("satellite", telecommand_config.get("satellite"))
            channel = item.get("channel", telecommand_config.get("channel"))

            notification = self.telecommand_client.send_async_telecommand_request(
                operation="notify",
                command_code=command_code,
                satellite=satellite,
                channel=channel,
                response_timeout_sec=notification_timeout_sec,
            )
            print(
                f"asynchronous telecommand notification succeeded: "
                f"case_id={sim_case.case_id}, index={index + 1}, "
                f"command_code={command_code}, "
                f"request_id={notification.get('requestId')}"
            )

            if _bool_config(item.get("sync_reset_start", False)):
                reset_start_payload = self.udp_client.send_initial_condition(
                    sim_case.initial_condition,
                    reset=True,
                )
                reset_start_sent = True
                print(
                    f"sent initial condition by UDP: "
                    f"case_id={sim_case.case_id}, reset=1, "
                    f"bytes={len(reset_start_payload)}, "
                    f"sync_with={command_code}"
                )

            execution = self.telecommand_client.send_async_telecommand_request(
                operation="execute",
                command_code=command_code,
                satellite=satellite,
                channel=channel,
                response_timeout_sec=execution_timeout_sec,
            )
            print(
                f"asynchronous telecommand execution succeeded: "
                f"case_id={sim_case.case_id}, index={index + 1}, "
                f"command_code={command_code}, "
                f"request_id={execution.get('requestId')}"
            )

        post_delay_sec = float(telecommand_config.get("post_delay_sec", 0.0))
        if post_delay_sec > 0:
            time.sleep(post_delay_sec)

        return reset_start_sent

    @staticmethod
    def _normalize_telecommand_item(item, index: int) -> dict:
        if isinstance(item, str):
            command_code = item.strip()
            normalized = {"command_code": command_code}
        elif isinstance(item, dict):
            normalized = dict(item)
            command_code = str(normalized.get("command_code", "")).strip()
            normalized["command_code"] = command_code
        else:
            raise ValueError(
                f"telecommand command item {index + 1} must be a string or "
                f"mapping: {item!r}"
            )

        if not command_code:
            raise ValueError(
                f"telecommand command item {index + 1} is missing command_code"
            )
        return normalized

    def _resolve_telecommand_config(self, sim_case: SimulationCase) -> dict:
        config = dict(self.telecommand_config)
        case_telecommand = sim_case.telecommand

        if case_telecommand is None:
            return config

        if isinstance(case_telecommand, dict):
            config.update(case_telecommand)
        else:
            config["command_code"] = case_telecommand

        return config

    def _receive_frames(
        self,
        sim_case: SimulationCase,
        stop_event: Event | None = None,
    ):
        original_codes = self.tcp_client.telemetry_poll_codes
        if sim_case.telemetry_codes:
            self.tcp_client.telemetry_poll_codes = list(sim_case.telemetry_codes)
        try:
            yield from self.tcp_client.receive_frames(
                max_duration_sec=self.max_duration_sec,
                stop_event=stop_event,
            )
        finally:
            self.tcp_client.telemetry_poll_codes = original_codes

    def close(self) -> None:
        """Close persistent TCP connections when the test session is ending."""
        self.tcp_client.close()
        self.telecommand_client.close()


def _bool_config(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
