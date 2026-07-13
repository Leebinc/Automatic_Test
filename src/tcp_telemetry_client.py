import socket
import time
from collections.abc import Iterator

from src.models import TelemetryFrame
from src.protocols import (
    decode_tcp_json_response,
    decode_telemetry_response_frame,
    decode_telemetry_response_parameters,
    encode_tcp_json_command,
    encode_tcp_payload,
)


class TcpTelemetryClient:
    def __init__(
        self,
        host: str,
        port: int,
        timeout_sec: float = 2.0,
        recv_buffer_size: int = 4096,
        connect_retry_timeout_sec: float = 20.0,
        connect_retry_interval_sec: float = 0.5,
        telemetry_poll_codes: list[str] | None = None,
        telemetry_frame_field_codes: dict | None = None,
        poll_interval_sec: float = 0.1,
    ):
        self.host = host
        self.port = port
        self.timeout_sec = timeout_sec
        self.recv_buffer_size = recv_buffer_size
        self.connect_retry_timeout_sec = connect_retry_timeout_sec
        self.connect_retry_interval_sec = connect_retry_interval_sec
        self.telemetry_poll_codes = telemetry_poll_codes or []
        self.telemetry_frame_field_codes = telemetry_frame_field_codes or {}
        self.poll_interval_sec = poll_interval_sec

    def _connect(self) -> socket.socket:
        deadline = time.monotonic() + self.connect_retry_timeout_sec
        last_error = None

        while True:
            try:
                sock = socket.create_connection(
                    (self.host, self.port),
                    timeout=self.timeout_sec,
                )
                sock.settimeout(self.timeout_sec)
                return sock
            except OSError as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    break
                time.sleep(self.connect_retry_interval_sec)

        raise TimeoutError(
            f"could not connect to TCP telemetry server "
            f"{self.host}:{self.port} within "
            f"{self.connect_retry_timeout_sec}s"
        ) from last_error

    def ping(self) -> dict:
        return self.request_once({"cmd": "ping"})

    def get_parameter(self, tm_code: str):
        response = self.request_once({"cmd": "get", "tmCode": tm_code})
        parameters = decode_telemetry_response_parameters(response)
        return parameters[0] if parameters else None

    def list_parameters(self, tm_codes: list[str]):
        response = self.request_once({"cmd": "list", "tmCodes": tm_codes})
        return decode_telemetry_response_parameters(response)

    def request_once(self, command: dict) -> dict:
        with self._connect() as sock:
            self._send_command(sock, command)
            return self._receive_response(sock, b"")[0]

    def send_payload(
        self,
        payload,
        expect_response: bool = True,
        append_newline: bool = True,
    ) -> dict | None:
        with self._connect() as sock:
            sock.sendall(encode_tcp_payload(payload, append_newline=append_newline))
            if not expect_response:
                return None
            return self._receive_response(sock, b"")[0]

    def receive_frames(
        self,
        max_duration_sec: float | None = None,
    ) -> Iterator[TelemetryFrame]:
        if not self.telemetry_poll_codes:
            raise ValueError(
                "tcp.telemetry_poll_codes must be configured for the JSON "
                "request-response telemetry protocol"
            )

        yield from self._poll_frames(max_duration_sec=max_duration_sec)

    def _poll_frames(
        self,
        max_duration_sec: float | None = None,
    ) -> Iterator[TelemetryFrame]:
        buffer = b""
        command = {"cmd": "list", "tmCodes": self.telemetry_poll_codes}

        with self._connect() as sock:
            start_time = time.monotonic()

            while True:
                if (
                    max_duration_sec is not None
                    and time.monotonic() - start_time >= max_duration_sec
                ):
                    return

                self._send_command(sock, command)
                response, buffer = self._receive_response(sock, buffer)
                frame = decode_telemetry_response_frame(
                    response=response,
                    field_codes=self.telemetry_frame_field_codes,
                )
                yield frame

                time.sleep(self.poll_interval_sec)

    def _send_command(self, sock: socket.socket, command: dict) -> None:
        sock.sendall(encode_tcp_json_command(command))

    def _receive_response(
        self,
        sock: socket.socket,
        buffer: bytes,
    ) -> tuple[dict, bytes]:
        while b"\n" not in buffer:
            data = sock.recv(self.recv_buffer_size)
            if not data:
                raise ConnectionError("TCP telemetry server closed the connection")
            buffer += data

        line, buffer = buffer.split(b"\n", 1)
        line = line.strip()
        if not line:
            return self._receive_response(sock, buffer)

        return decode_tcp_json_response(line), buffer
