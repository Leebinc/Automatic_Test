import argparse
import json
import time

from src.runner import load_yaml
from src.tcp_telemetry_client import TcpTelemetryClient


ENV_CONFIG_PATH = "config/env.yaml"


def parse_tm_codes(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def build_client(args) -> TcpTelemetryClient:
    env_config = load_yaml(args.env)
    tcp_config = env_config.get("tcp", {})

    host = args.host or tcp_config.get("host", "127.0.0.1")
    port = int(args.port or tcp_config.get("port", 502))
    tm_codes = parse_tm_codes(args.tm_codes) or tcp_config.get(
        "telemetry_poll_codes",
        [],
    )

    return TcpTelemetryClient(
        host=host,
        port=port,
        timeout_sec=float(tcp_config.get("timeout_sec", 2.0)),
        recv_buffer_size=int(tcp_config.get("recv_buffer_size", 4096)),
        connect_retry_timeout_sec=float(
            tcp_config.get("connect_retry_timeout_sec", 20.0)
        ),
        connect_retry_interval_sec=float(
            tcp_config.get("connect_retry_interval_sec", 0.5)
        ),
        telemetry_poll_codes=tm_codes,
        telemetry_frame_field_codes=tcp_config.get("telemetry_frame_field_codes"),
        poll_interval_sec=args.interval,
    )


def print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_ping(client: TcpTelemetryClient) -> None:
    print_json(client.ping())


def run_get(client: TcpTelemetryClient, tm_code: str) -> None:
    parameter = client.get_parameter(tm_code)
    print_json(parameter.raw if parameter is not None else None)


def run_list(client: TcpTelemetryClient, tm_codes: list[str]) -> None:
    parameters = client.list_parameters(tm_codes)
    print_json([item.raw for item in parameters])


def run_poll(client: TcpTelemetryClient, duration_sec: float) -> None:
    start_time = time.monotonic()
    for frame in client.receive_frames(max_duration_sec=duration_sec):
        print(
            "frame "
            f"t={frame.timestamp_sec:.3f}s "
            f"rate={frame.angular_rate_deg_s} "
            f"mode={frame.control_mode} "
            f"status={frame.sim_status}"
        )
        print_json(frame.raw)

        if time.monotonic() - start_time >= duration_sec:
            break


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Joint-debug tool for the JSON TCP telemetry protocol."
    )
    parser.add_argument(
        "command",
        choices=("ping", "get", "list", "poll"),
        help="TCP telemetry command to run.",
    )
    parser.add_argument("--env", default=ENV_CONFIG_PATH)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--tm-code", help="Telemetry code for get.")
    parser.add_argument(
        "--tm-codes",
        help="Comma-separated telemetry codes for list/poll.",
    )
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    client = build_client(args)

    if args.command == "ping":
        run_ping(client)
    elif args.command == "get":
        if not args.tm_code:
            parser.error("--tm-code is required for get")
        run_get(client, args.tm_code)
    elif args.command == "list":
        tm_codes = parse_tm_codes(args.tm_codes)
        if not tm_codes:
            parser.error("--tm-codes is required for list")
        run_list(client, tm_codes)
    elif args.command == "poll":
        if not client.telemetry_poll_codes:
            parser.error("--tm-codes or tcp.telemetry_poll_codes is required for poll")
        run_poll(client, args.duration)


if __name__ == "__main__":
    main()
