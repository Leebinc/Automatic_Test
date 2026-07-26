import argparse
import json
import threading
import time

from src.runner import load_yaml
from src.tcp_telemetry_client import TcpTelemetryClient


ENV_CONFIG_PATH = "config/env.yaml"


def parse_tm_codes(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def build_client(args, use_telecommand_config: bool = False) -> TcpTelemetryClient:
    env_config = load_yaml(args.env)
    tcp_config = env_config.get("tcp", {})
    selected_config = (
        env_config.get("telecommand", {})
        if use_telecommand_config
        else tcp_config
    )

    host = args.host or selected_config.get("host", tcp_config.get("host", "127.0.0.1"))
    port = int(args.port or selected_config.get("port", tcp_config.get("port", 502)))
    tm_codes = parse_tm_codes(args.tm_codes) or selected_config.get(
        "telemetry_poll_codes",
        tcp_config.get("telemetry_poll_codes", []),
    )
    frame_field_codes = selected_config.get(
        "telemetry_frame_field_codes",
        tcp_config.get("telemetry_frame_field_codes"),
    )
    timeout_sec = float(
        selected_config.get("timeout_sec", tcp_config.get("timeout_sec", 2.0))
    )
    recv_buffer_size = int(
        selected_config.get(
            "recv_buffer_size",
            tcp_config.get("recv_buffer_size", 4096),
        )
    )
    connect_retry_timeout_sec = float(
        selected_config.get(
            "connect_retry_timeout_sec",
            tcp_config.get("connect_retry_timeout_sec", 20.0),
        )
    )
    connect_retry_interval_sec = float(
        selected_config.get(
            "connect_retry_interval_sec",
            tcp_config.get("connect_retry_interval_sec", 0.5),
        )
    )
    poll_interval_sec = float(
        selected_config.get(
            "poll_interval_sec",
            tcp_config.get("poll_interval_sec", args.interval),
        )
    )
    heartbeat_interval_sec = selected_config.get(
        "heartbeat_interval_sec",
        tcp_config.get("heartbeat_interval_sec"),
    )
    if heartbeat_interval_sec is not None:
        heartbeat_interval_sec = float(heartbeat_interval_sec)
    return TcpTelemetryClient(
        host=host,
        port=port,
        timeout_sec=timeout_sec,
        recv_buffer_size=recv_buffer_size,
        connect_retry_timeout_sec=connect_retry_timeout_sec,
        connect_retry_interval_sec=connect_retry_interval_sec,
        telemetry_poll_codes=tm_codes,
        telemetry_frame_field_codes=frame_field_codes,
        poll_interval_sec=poll_interval_sec,
        heartbeat_interval_sec=heartbeat_interval_sec,
    )


def print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_interactive_request(value: str) -> dict:
    parts = value.strip().split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command == "ping":
        return {"cmd": "ping"}
    if command == "get" and argument:
        return {"cmd": "get", "tmCode": argument}
    if command == "list":
        tm_codes = parse_tm_codes(argument)
        if tm_codes:
            return {"cmd": "list", "tmCodes": tm_codes}

    raise ValueError("use: ping | get TM_CODE | list CODE1,CODE2")


def run_request_and_wait(client: TcpTelemetryClient, command: dict) -> None:
    sock = client.open_connection()
    print(f"connected to {client.host}:{client.port}")
    client.send_command_on_connection(sock, command)
    print("initial request sent; the same connection remains available for more requests")
    print("commands: ping | get TM_CODE | list CODE1,CODE2 | help | quit")

    stop_event = threading.Event()

    def receive_worker() -> None:
        close_reason, _ = client.receive_responses_until_closed(
            sock,
            on_response=print_json,
            stop_event=stop_event,
        )
        if not stop_event.is_set():
            print(f"\n{close_reason}")
        stop_event.set()

    receiver = threading.Thread(
        target=receive_worker,
        name="tcp-joint-debug-receiver",
        daemon=True,
    )
    receiver.start()

    try:
        while not stop_event.is_set():
            try:
                value = input("tcp> ").strip()
            except EOFError:
                break

            if not value:
                continue
            if value.lower() in {"quit", "exit"}:
                break
            if value.lower() == "help":
                print("commands: ping | get TM_CODE | list CODE1,CODE2 | help | quit")
                continue

            try:
                next_command = parse_interactive_request(value)
            except ValueError as exc:
                print(exc)
                continue

            client.send_command_on_connection(sock, next_command)
            print(f"sent: {json.dumps(next_command, ensure_ascii=False)}")
    finally:
        stop_event.set()
        receiver.join(timeout=2.0)


def run_ping(client: TcpTelemetryClient) -> None:
    run_request_and_wait(client=client, command={"cmd": "ping"})


def run_get(client: TcpTelemetryClient, tm_code: str) -> None:
    run_request_and_wait(
        client=client,
        command={"cmd": "get", "tmCode": tm_code},
    )


def run_list(client: TcpTelemetryClient, tm_codes: list[str]) -> None:
    run_request_and_wait(
        client=client,
        command={"cmd": "list", "tmCodes": tm_codes},
    )


def run_poll(client: TcpTelemetryClient, duration_sec: float | None) -> None:
    start_time = time.monotonic()
    for frame in client.receive_frames(max_duration_sec=duration_sec):
        print(
            "frame "
            f"t={frame.timestamp_sec:.3f}s "
            f"angle={frame.attitude_angle_deg} "
            f"rate={frame.angular_rate_deg_s} "
            f"mode={frame.control_mode} "
            f"attitude_reference={frame.attitude_reference}"
        )
        print_json(frame.raw)

        if (
            duration_sec is not None
            and time.monotonic() - start_time >= duration_sec
        ):
            break


def run_async_telecommand(
    client: TcpTelemetryClient,
    command_code: str,
    satellite: str | None,
    channel: str | None,
    notification_timeout_sec: float,
    execution_timeout_sec: float,
) -> None:
    print(f"connected to {client.host}:{client.port}")
    notification = client.send_async_telecommand_request(
        operation="notify",
        command_code=command_code,
        satellite=satellite,
        channel=channel,
        response_timeout_sec=notification_timeout_sec,
    )
    print("notification succeeded:")
    print_json(notification)

    execution = client.send_async_telecommand_request(
        operation="execute",
        command_code=command_code,
        satellite=satellite,
        channel=channel,
        response_timeout_sec=execution_timeout_sec,
    )
    print("execution succeeded:")
    print_json(execution)
    print("persistent telecommand connection remains open")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Joint-debug tool for TCP telemetry and asynchronous telecommands."
    )
    parser.add_argument(
        "command",
        choices=("ping", "get", "list", "poll", "telecommand"),
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
    parser.add_argument(
        "--duration",
        type=float,
        help="Optional poll duration; omit to run until Ctrl+C or server close.",
    )
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--command-code", help="Telecommand code for notify/execute.")
    parser.add_argument("--satellite", help="Optional target satellite identifier.")
    parser.add_argument("--channel", help="Optional asynchronous channel identifier.")
    parser.add_argument("--notification-timeout", type=float)
    parser.add_argument("--execution-timeout", type=float)
    args = parser.parse_args()

    client = build_client(
        args,
        use_telecommand_config=args.command == "telecommand",
    )

    try:
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
        elif args.command == "telecommand":
            if not args.command_code:
                parser.error("--command-code is required for telecommand")
            env_config = load_yaml(args.env)
            telecommand_config = env_config.get("telecommand", {})
            run_async_telecommand(
                client=client,
                command_code=args.command_code,
                satellite=args.satellite or telecommand_config.get("satellite"),
                channel=args.channel or telecommand_config.get("channel"),
                notification_timeout_sec=float(
                    args.notification_timeout
                    or telecommand_config.get("notification_timeout_sec", 30.0)
                ),
                execution_timeout_sec=float(
                    args.execution_timeout
                    or telecommand_config.get("execution_timeout_sec", 60.0)
                ),
            )
    except KeyboardInterrupt:
        print("interrupted by user; closing persistent TCP connection")
    finally:
        client.close()


if __name__ == "__main__":
    main()
