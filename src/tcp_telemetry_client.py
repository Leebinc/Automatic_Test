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
        poll_interval_sec: float = 1.0,
        heartbeat_interval_sec: float | None = None,
        idle_disconnect_sec: float = 10.0,
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
        self.heartbeat_interval_sec = heartbeat_interval_sec
        self.idle_disconnect_sec = idle_disconnect_sec

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

    def open_connection(self) -> socket.socket:
        """Open a TCP connection whose lifetime is controlled by the caller."""
        return self._connect()

    def request_on_connection(
        self,
        sock: socket.socket,
        command: dict,
        buffer: bytes = b"",
    ) -> tuple[dict, bytes]:
        """Send one JSON command without closing the existing connection."""
        self._send_command(sock, command)
        return self._receive_response(sock, buffer)

    def send_command_on_connection(
        self,
        sock: socket.socket,
        command: dict,
    ) -> None:
        """Send one JSON command without requiring a response."""
        self._send_command(sock, command)

    def receive_responses_until_idle(
        self,
        sock: socket.socket,
        on_response,
        idle_timeout_sec: float | None = None,
    ) -> tuple[str, int]:
        """Receive zero or more JSON responses until the connection is idle."""
        idle_timeout_sec = (
            self.idle_disconnect_sec
            if idle_timeout_sec is None
            else float(idle_timeout_sec)
        )
        if idle_timeout_sec <= 0:
            return "client idle wait disabled; closing connection", 0

        buffer = b""
        response_count = 0
        deadline = time.monotonic() + idle_timeout_sec

        while True:
            remaining_sec = deadline - time.monotonic()
            if remaining_sec <= 0:
                return self._idle_timeout_reason(
                    idle_timeout_sec,
                    buffer,
                ), response_count

            sock.settimeout(remaining_sec)
            try:
                data = sock.recv(self.recv_buffer_size)
            except socket.timeout:
                return self._idle_timeout_reason(
                    idle_timeout_sec,
                    buffer,
                ), response_count
            except OSError as exc:
                return f"server connection ended with socket error: {exc}", response_count

            if not data:
                return "server closed the connection", response_count

            buffer += data
            deadline = time.monotonic() + idle_timeout_sec

            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                on_response(decode_tcp_json_response(line))
                response_count += 1

            # Also accept one complete JSON object without a newline. This is
            # useful when the peer treats an idle TCP period as the frame end.
            if buffer.strip():
                try:
                    response = decode_tcp_json_response(buffer.strip())
                except (ValueError, UnicodeError):
                    pass
                else:
                    on_response(response)
                    response_count += 1
                    buffer = b""

    def _idle_timeout_reason(
        self,
        idle_timeout_sec: float,
        buffer: bytes,
    ) -> str:
        if buffer:
            preview = buffer[:64].hex(" ")
            return (
                f"client idle timeout reached after {idle_timeout_sec:.3f}s; "
                f"closing connection with {len(buffer)} unparsed bytes "
                f"(hex={preview})"
            )
        return (
            f"client idle timeout reached after {idle_timeout_sec:.3f}s; "
            f"closing connection"
        )

    def wait_for_idle_disconnect(
        self,
        sock: socket.socket,
        idle_timeout_sec: float | None = None,
        buffered: bytes = b"",
    ) -> str:
        """Wait until the server closes or the local idle timeout is reached."""
        idle_timeout_sec = (
            self.idle_disconnect_sec
            if idle_timeout_sec is None
            else float(idle_timeout_sec)
        )
        if idle_timeout_sec <= 0:
            return "client idle wait disabled; closing connection"

        extra_bytes = len(buffered)
        deadline = time.monotonic() + idle_timeout_sec

        while True:
            remaining_sec = deadline - time.monotonic()
            if remaining_sec <= 0:
                return (
                    f"client idle timeout reached after {idle_timeout_sec:.3f}s; "
                    f"closing connection (extra_bytes={extra_bytes})"
                )

            sock.settimeout(remaining_sec)
            try:
                data = sock.recv(self.recv_buffer_size)
            except socket.timeout:
                return (
                    f"client idle timeout reached after {idle_timeout_sec:.3f}s; "
                    f"closing connection (extra_bytes={extra_bytes})"
                )
            except OSError as exc:
                return f"server connection ended with socket error: {exc}"

            if not data:
                return "server closed the idle connection"

            extra_bytes += len(data)
            deadline = time.monotonic() + idle_timeout_sec

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

    def send_payload_on_connection(
        self,
        sock: socket.socket,
        payload,
        append_newline: bool = False,
    ) -> int:
        """Send one payload on an existing connection and return its byte count."""
        data = encode_tcp_payload(payload, append_newline=append_newline)
        sock.sendall(data)
        return len(data)

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
            last_heartbeat_time = start_time

            while True:
                now = time.monotonic()
                if (
                    max_duration_sec is not None
                    and now - start_time >= max_duration_sec
                ):
                    return

                if self._heartbeat_due(now, last_heartbeat_time):
                    self._send_command(sock, {"cmd": "ping"})
                    response, buffer = self._receive_response(sock, buffer)
                    self._validate_heartbeat_response(response)
                    last_heartbeat_time = now

                self._send_command(sock, command)
                response, buffer = self._receive_response(sock, buffer)
                frame = decode_telemetry_response_frame(
                    response=response,
                    field_codes=self.telemetry_frame_field_codes,
                )
                # Convergence duration uses local monotonic elapsed time, not
                # spacecraft/system telemetry time.
                frame.timestamp_sec = time.monotonic() - start_time
                yield frame

                time.sleep(self.poll_interval_sec)

    def _heartbeat_due(self, now: float, last_heartbeat_time: float) -> bool:
        return (
            self.heartbeat_interval_sec is not None
            and self.heartbeat_interval_sec > 0
            and now - last_heartbeat_time >= self.heartbeat_interval_sec
        )

    def _validate_heartbeat_response(self, response: dict) -> None:
        code = int(response.get("code", -1))
        if code != 0:
            raise ConnectionError(
                f"TCP telemetry heartbeat failed: "
                f"code={code}, msg={response.get('msg', '')}"
            )

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
