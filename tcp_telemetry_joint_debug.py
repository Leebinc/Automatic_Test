import argparse
import json
import time

from src.runner import load_yaml
from src.tcp_telemetry_client import TcpTelemetryClient
from src.protocols import decode_telemetry_response_parameters, encode_hex_source


ENV_CONFIG_PATH = "config/env.yaml"


def parse_tm_codes(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_payload(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


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
    idle_disconnect_sec = float(
        args.idle_timeout
        if args.idle_timeout is not None
        else selected_config.get(
            "idle_disconnect_sec",
            tcp_config.get("idle_disconnect_sec", 10.0),
        )
    )

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
        idle_disconnect_sec=idle_disconnect_sec,
    )


def print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_request_and_wait(client: TcpTelemetryClient, command: dict, print_response) -> None:
    with client.open_connection() as sock:
        print(f"connected to {client.host}:{client.port}")
        client.send_command_on_connection(sock, command)
        print(
            f"request sent; connection remains open while waiting up to "
            f"{client.idle_disconnect_sec:.3f}s without incoming data"
        )
        close_reason, response_count = client.receive_responses_until_idle(
            sock,
            on_response=print_response,
        )

    if response_count == 0:
        print("no response received; this is allowed for a receive-only peer")
    print(close_reason)


def run_ping(client: TcpTelemetryClient) -> None:
    run_request_and_wait(
        client=client,
        command={"cmd": "ping"},
        print_response=print_json,
    )


def run_get(client: TcpTelemetryClient, tm_code: str) -> None:
    def print_get_response(response: dict) -> None:
        parameters = decode_telemetry_response_parameters(response)
        parameter = parameters[0] if parameters else None
        print_json(parameter.raw if parameter is not None else None)

    run_request_and_wait(
        client=client,
        command={"cmd": "get", "tmCode": tm_code},
        print_response=print_get_response,
    )


def run_list(client: TcpTelemetryClient, tm_codes: list[str]) -> None:
    def print_list_response(response: dict) -> None:
        parameters = decode_telemetry_response_parameters(response)
        print_json([item.raw for item in parameters])

    run_request_and_wait(
        client=client,
        command={"cmd": "list", "tmCodes": tm_codes},
        print_response=print_list_response,
    )


def run_poll(client: TcpTelemetryClient, duration_sec: float) -> None:
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

        if time.monotonic() - start_time >= duration_sec:
            break


def run_send(
    client: TcpTelemetryClient,
    payload,
    expect_response: bool,
    append_newline: bool,
) -> None:
    response = client.send_payload(
        payload=payload,
        expect_response=expect_response,
        append_newline=append_newline,
    )
    if response is not None:
        print_json(response)


def prepare_telecommand_sequence(telecommand_config: dict) -> list[tuple[str, bytes]]:
    sequence = telecommand_config.get("sequence", [])
    if not isinstance(sequence, list) or not sequence:
        raise ValueError("telecommand.sequence must be a non-empty list")

    prepared = []
    for index, item in enumerate(sequence, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"telecommand sequence item {index} must be a mapping: {item!r}"
            )

        name = str(item.get("name", f"command_{index}"))
        hex_source = item.get("hex")
        if not hex_source:
            raise ValueError(f"telecommand {name} is missing hex source")

        prepared.append((name, encode_hex_source(str(hex_source))))

    return prepared


def run_send_sequence(
    client: TcpTelemetryClient,
    telecommand_config: dict,
) -> None:
    prepared = prepare_telecommand_sequence(telecommand_config)
    initial_delay_sec = float(telecommand_config.get("initial_delay_sec", 0.0))
    interval_sec = float(telecommand_config.get("interval_sec", 0.1))
    post_delay_sec = float(telecommand_config.get("post_delay_sec", 0.0))
    append_newline = bool(telecommand_config.get("append_newline", False))

    with client.open_connection() as sock:
        print(f"connected to {client.host}:{client.port}")
        print(
            f"loaded {len(prepared)} telecommands; "
            f"interval={interval_sec:.3f}s, append_newline={append_newline}"
        )

        if initial_delay_sec > 0:
            time.sleep(initial_delay_sec)

        for index, (name, payload) in enumerate(prepared, start=1):
            if index > 1 and interval_sec > 0:
                time.sleep(interval_sec)

            byte_count = client.send_payload_on_connection(
                sock=sock,
                payload=payload,
                append_newline=append_newline,
            )
            print(
                f"sent telecommand {index}/{len(prepared)}: "
                f"name={name}, bytes={byte_count}"
            )

        if post_delay_sec > 0:
            time.sleep(post_delay_sec)

        print(
            f"all {len(prepared)} telecommands sent; connection remains open "
            f"until server close or {client.idle_disconnect_sec:.3f}s idle timeout"
        )
        close_reason = client.wait_for_idle_disconnect(sock)

    print(close_reason)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Joint-debug tool for TCP telemetry and binary telecommands."
    )
    parser.add_argument(
        "command",
        choices=("ping", "get", "list", "poll", "send", "send-sequence"),
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
    parser.add_argument(
        "--idle-timeout",
        type=float,
        help=(
            "Seconds to keep ping/get/list/send-sequence connections open while idle; "
            "the timer resets whenever data is received and defaults to "
            "tcp.idle_disconnect_sec."
        ),
    )
    parser.add_argument("--payload", help="JSON or raw text payload for send.")
    parser.add_argument(
        "--hex",
        action="store_true",
        help="Treat --payload as hex source and send binary bytes.",
    )
    parser.add_argument(
        "--no-response",
        action="store_true",
        help="Do not wait for a TCP response after send.",
    )
    parser.add_argument(
        "--no-newline",
        action="store_true",
        help="Do not append newline to the send payload.",
    )
    args = parser.parse_args()

    client = build_client(
        args,
        use_telecommand_config=args.command in {"send", "send-sequence"},
    )

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
    elif args.command == "send":
        if args.payload is None:
            parser.error("--payload is required for send")
        payload = encode_hex_source(args.payload) if args.hex else parse_payload(args.payload)
        run_send(
            client=client,
            payload=payload,
            expect_response=not args.no_response,
            append_newline=not args.no_newline,
        )
    elif args.command == "send-sequence":
        env_config = load_yaml(args.env)
        run_send_sequence(
            client=client,
            telecommand_config=env_config.get("telecommand", {}),
        )


if __name__ == "__main__":
    main()
