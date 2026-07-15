import time

import yaml

from src.models import ExpectedResult, InitialCondition, SimulationCase
from src.protocols import encode_hex_source, encode_time_sync_hex_source
from src.tcp_telemetry_client import TcpTelemetryClient
from src.udp_client import UdpInitialConditionClient
from src.validators import is_attitude_angle_below_threshold


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
                telecommand=item.get("telecommand"),
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
        validation_config = env_config.get("validation", {})
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
        self.attitude_angle_threshold_deg = float(
            validation_config.get("attitude_angle_threshold_deg", 0.5)
        )
        self.convergence_hold_sec = float(
            validation_config.get("convergence_hold_sec", 5.0)
        )
        self.stop_when_converged = _bool_config(
            validation_config.get("stop_when_converged", True)
        )
        self.fail_fast_control_mode = _bool_config(
            validation_config.get("fail_fast_control_mode", True)
        )
        self.mode_check_grace_sec = float(
            validation_config.get("mode_check_grace_sec", 0.0)
        )
        self.fail_fast_attitude_reference = _bool_config(
            validation_config.get("fail_fast_attitude_reference", True)
        )
        self.attitude_reference_check_grace_sec = float(
            validation_config.get("attitude_reference_check_grace_sec", 0.0)
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

        frames = []
        start_time = time.monotonic()
        convergence_start_sec = None
        converged = False

        for frame in self._receive_frames():
            frames.append(frame)
            elapsed_sec = time.monotonic() - start_time

            self._assert_control_mode_if_needed(
                sim_case=sim_case,
                frame=frame,
                elapsed_sec=elapsed_sec,
            )
            self._assert_attitude_reference_if_needed(
                sim_case=sim_case,
                frame=frame,
                elapsed_sec=elapsed_sec,
            )

            frame_time_sec = frame.timestamp_sec if frame.timestamp_sec > 0 else elapsed_sec
            if is_attitude_angle_below_threshold(
                frame=frame,
                threshold_deg=self.attitude_angle_threshold_deg,
            ):
                if convergence_start_sec is None:
                    convergence_start_sec = frame_time_sec

                if frame_time_sec - convergence_start_sec >= self.convergence_hold_sec:
                    converged = True
                    if self.stop_when_converged:
                        print(
                            f"stop receiving telemetry because attitude angle "
                            f"converged for {self.convergence_hold_sec}s"
                        )
                        break
            else:
                convergence_start_sec = None

            if elapsed_sec >= self.max_duration_sec:
                print(
                    f"stop receiving telemetry because max_duration_sec "
                    f"was reached: {self.max_duration_sec}s"
                )
                break

        print(f"received telemetry frames: {len(frames)}, converged={converged}")
        return frames

    def _send_telecommand_if_configured(self, sim_case: SimulationCase) -> bool:
        telecommand_config = self._resolve_telecommand_config(sim_case)
        payload = telecommand_config.get("payload")
        sequence = telecommand_config.get("sequence")
        enabled = _bool_config(
            telecommand_config.get(
                "enabled",
                payload is not None or sequence is not None,
            )
        )

        if not enabled:
            return False

        if sequence:
            return self._send_telecommand_sequence(sim_case, telecommand_config)

        if payload is None:
            return False

        expect_response = _bool_config(
            telecommand_config.get("expect_response", True)
        )
        append_newline = _bool_config(
            telecommand_config.get("append_newline", True)
        )
        response = self.telecommand_client.send_payload(
            payload=payload,
            expect_response=expect_response,
            append_newline=append_newline,
        )
        print(
            f"sent telecommand by TCP: "
            f"case_id={sim_case.case_id}, expect_response={expect_response}"
        )
        if response is not None:
            print(f"telecommand response: {response}")

        post_delay_sec = float(telecommand_config.get("post_delay_sec", 0.0))
        if post_delay_sec > 0:
            time.sleep(post_delay_sec)

        return False

    def _send_telecommand_sequence(
        self,
        sim_case: SimulationCase,
        telecommand_config: dict,
    ) -> bool:
        sequence = telecommand_config.get("sequence", [])
        if not isinstance(sequence, list) or not sequence:
            return False

        interval_sec = float(telecommand_config.get("interval_sec", 0.1))
        expect_response = _bool_config(
            telecommand_config.get("expect_response", False)
        )
        append_newline = _bool_config(
            telecommand_config.get("append_newline", False)
        )
        epoch_year = int(telecommand_config.get("time_epoch_year", 2006))
        reset_start_sent = False

        initial_delay_sec = float(telecommand_config.get("initial_delay_sec", 0.0))
        if initial_delay_sec > 0:
            time.sleep(initial_delay_sec)

        for index, item in enumerate(sequence):
            if index > 0 and interval_sec > 0:
                time.sleep(interval_sec)

            if not isinstance(item, dict):
                raise ValueError(f"telecommand sequence item must be a dict: {item!r}")

            name = str(item.get("name", f"command_{index + 1}"))
            hex_source = item.get("hex")
            if not hex_source:
                raise ValueError(f"telecommand {name} is missing hex source")

            if _bool_config(item.get("sync_reset_start", False)):
                reset_start_payload = self.udp_client.send_initial_condition(
                    sim_case.initial_condition,
                    reset=True,
                )
                reset_start_sent = True
                print(
                    f"sent initial condition by UDP: "
                    f"case_id={sim_case.case_id}, reset=1, "
                    f"bytes={len(reset_start_payload)}, sync_with={name}"
                )

            if _bool_config(item.get("time_sync", False)):
                payload = encode_time_sync_hex_source(
                    hex_source=hex_source,
                    condition=sim_case.initial_condition,
                    placeholder_hex=str(item["time_placeholder_hex"]),
                    epoch_year=epoch_year,
                )
            else:
                payload = encode_hex_source(str(hex_source))

            response = self.telecommand_client.send_payload(
                payload=payload,
                expect_response=expect_response,
                append_newline=append_newline,
            )
            print(
                f"sent telecommand by TCP: "
                f"case_id={sim_case.case_id}, index={index + 1}, "
                f"name={name}, bytes={len(payload)}"
            )
            if response is not None:
                print(f"telecommand response: {response}")

        post_delay_sec = float(telecommand_config.get("post_delay_sec", 0.0))
        if post_delay_sec > 0:
            time.sleep(post_delay_sec)

        return reset_start_sent

    def _resolve_telecommand_config(self, sim_case: SimulationCase) -> dict:
        config = dict(self.telecommand_config)
        case_telecommand = sim_case.telecommand

        if case_telecommand is None:
            return config

        if isinstance(case_telecommand, dict) and (
            "payload" in case_telecommand or "enabled" in case_telecommand
        ):
            config.update(case_telecommand)
        else:
            config["payload"] = case_telecommand

        return config

    def _assert_control_mode_if_needed(
        self,
        sim_case: SimulationCase,
        frame,
        elapsed_sec: float,
    ) -> None:
        if not self.fail_fast_control_mode:
            return

        if elapsed_sec < self.mode_check_grace_sec:
            return

        expected_mode = sim_case.expected.final_control_mode
        if not expected_mode:
            return

        if frame.control_mode != expected_mode:
            raise AssertionError(
                f"{sim_case.case_id} control mode mismatch during telemetry; "
                f"expected={expected_mode}, actual={frame.control_mode}, "
                f"t={frame.timestamp_sec:.3f}s, "
                f"rate={frame.angular_rate_deg_s}"
            )

    def _assert_attitude_reference_if_needed(
        self,
        sim_case: SimulationCase,
        frame,
        elapsed_sec: float,
    ) -> None:
        if not self.fail_fast_attitude_reference:
            return

        if elapsed_sec < self.attitude_reference_check_grace_sec:
            return

        expected_reference = sim_case.expected.final_attitude_reference
        if not expected_reference:
            return

        if frame.attitude_reference != expected_reference:
            raise AssertionError(
                f"{sim_case.case_id} attitude reference mismatch during telemetry; "
                f"expected={expected_reference}, "
                f"actual={frame.attitude_reference}, "
                f"t={frame.timestamp_sec:.3f}s, "
                f"angle={frame.attitude_angle_deg}, "
                f"rate={frame.angular_rate_deg_s}"
            )

    def _receive_frames(self):
        yield from self.tcp_client.receive_frames(
            max_duration_sec=self.max_duration_sec
        )

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
