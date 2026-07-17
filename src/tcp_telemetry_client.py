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
        connect_retry_interval_sec: float = 5,
        telemetry_poll_codes: list[str] | None = None,
        telemetry_frame_field_codes: dict | None = None,
        poll_interval_sec: float = 1.0,
        heartbeat_interval_sec: float | None = None,
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
        self._socket: socket.socket | None = None
        self._receive_buffer = b""

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
        sock = self.open_connection()
        self._send_command(sock, command)
        response, self._receive_buffer = self._receive_response(
            sock,
            self._receive_buffer,
        )
        return response

    def open_connection(self) -> socket.socket:
        """Return the process-level persistent TCP connection."""
        if self._socket is None or self._socket.fileno() < 0:
            self._socket = self._connect()
            self._receive_buffer = b""
        return self._socket

    def close(self) -> None:
        """Close the persistent connection when the client process is ending."""
        sock = self._socket
        self._socket = None
        self._receive_buffer = b""
        if sock is None:
            return

        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            sock.close()

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

    def receive_responses_until_closed(
        self,
        sock: socket.socket,
        on_response,
        stop_event=None,
    ) -> tuple[str, int]:
        """Receive optional JSON responses until interrupted or peer close."""
        buffer = self._receive_buffer if sock is self._socket else b""
        response_count = 0

        while True:
            if stop_event is not None and stop_event.is_set():
                return "client receive loop stopped", response_count

            sock.settimeout(min(max(self.timeout_sec, 0.1), 1.0))
            try:
                data = sock.recv(self.recv_buffer_size)
            except socket.timeout:
                continue
            except OSError as exc:
                self._mark_disconnected(sock)
                return f"server connection ended with socket error: {exc}", response_count

            if not data:
                self._mark_disconnected(sock)
                return "server closed the connection", response_count

            buffer += data
            buffer, parsed_count = self._dispatch_json_responses(
                buffer,
                on_response,
            )
            response_count += parsed_count
            if sock is self._socket:
                self._receive_buffer = buffer

    def wait_until_closed(self, sock: socket.socket) -> str:
        """Keep a connection open until interrupted or the peer closes it."""
        received_bytes = 0

        while True:
            sock.settimeout(min(max(self.timeout_sec, 0.1), 1.0))
            try:
                data = sock.recv(self.recv_buffer_size)
            except socket.timeout:
                continue
            except OSError as exc:
                self._mark_disconnected(sock)
                return f"server connection ended with socket error: {exc}"

            if not data:
                self._mark_disconnected(sock)
                return "server closed the connection"

            received_bytes += len(data)
            print(f"received {len(data)} unexpected bytes (total={received_bytes})")

    def _dispatch_json_responses(self, buffer: bytes, on_response) -> tuple[bytes, int]:
        response_count = 0

        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            on_response(decode_tcp_json_response(line))
            response_count += 1

        if buffer.strip():
            try:
                response = decode_tcp_json_response(buffer.strip())
            except (ValueError, UnicodeError):
                pass
            else:
                on_response(response)
                response_count += 1
                buffer = b""

        return buffer, response_count

    def _mark_disconnected(self, sock: socket.socket) -> None:
        if sock is not self._socket:
            return
        try:
            sock.close()
        finally:
            self._socket = None
            self._receive_buffer = b""

    def send_payload(
        self,
        payload,
        expect_response: bool = True,
        append_newline: bool = True,
    ) -> dict | None:
        sock = self.open_connection()
        self.send_payload_on_connection(
            sock,
            payload,
            append_newline=append_newline,
        )
        if not expect_response:
            return None
        response, self._receive_buffer = self._receive_response(
            sock,
            self._receive_buffer,
        )
        return response

    def send_payload_on_connection(
        self,
        sock: socket.socket,
        payload,
        append_newline: bool = False,
    ) -> int:
        """Send one payload on an existing connection and return its byte count."""
        data = encode_tcp_payload(payload, append_newline=append_newline)
        try:
            sock.sendall(data)
        except OSError:
            self._mark_disconnected(sock)
            raise
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
        command = {"cmd": "list", "tmCodes": self.telemetry_poll_codes}
        sock = self.open_connection()
        start_time = time.monotonic()
        operation_deadline = (
            start_time + max_duration_sec
            if max_duration_sec is not None
            else None
        )
        last_heartbeat_time = start_time
        next_poll_time = start_time
        poll_interval_sec = max(self.poll_interval_sec, 0.01)

        while True:
            now = time.monotonic()
            if operation_deadline is not None and now >= operation_deadline:
                return

            if self._heartbeat_due(now, last_heartbeat_time):
                self._send_command(sock, {"cmd": "ping"})
                last_heartbeat_time = now

            if now >= next_poll_time:
                self._send_command(sock, command)
                next_poll_time = now + poll_interval_sec

            receive_deadline = next_poll_time
            if operation_deadline is not None:
                receive_deadline = min(receive_deadline, operation_deadline)

            response, self._receive_buffer = self._receive_response_until(
                sock,
                self._receive_buffer,
                deadline=receive_deadline,
            )
            if response is None:
                continue

            if self._is_heartbeat_response(response):
                self._validate_heartbeat_response(response)
                continue

            frame = decode_telemetry_response_frame(
                response=response,
                field_codes=self.telemetry_frame_field_codes,
            )
            # Convergence duration uses local monotonic elapsed time, not
            # spacecraft/system telemetry time.
            frame.timestamp_sec = time.monotonic() - start_time
            yield frame

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

    def _is_heartbeat_response(self, response: dict) -> bool:
        return (
            int(response.get("code", -1)) == 0
            and str(response.get("msg", "")).lower() == "pong"
            and response.get("data") in (None, [], {})
        )

    def _send_command(self, sock: socket.socket, command: dict) -> None:
        try:
            sock.sendall(encode_tcp_json_command(command))
        except OSError:
            self._mark_disconnected(sock)
            raise

    def _receive_response(
        self,
        sock: socket.socket,
        buffer: bytes,
        deadline: float | None = None,
    ) -> tuple[dict, bytes]:
        while b"\n" not in buffer:
            if deadline is not None:
                remaining_sec = deadline - time.monotonic()
                if remaining_sec <= 0:
                    raise TimeoutError("telemetry response operation deadline reached")
                sock.settimeout(min(self.timeout_sec, remaining_sec))
            else:
                sock.settimeout(self.timeout_sec)

            try:
                data = sock.recv(self.recv_buffer_size)
            except socket.timeout:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("telemetry response operation deadline reached")
                continue
            except OSError:
                self._mark_disconnected(sock)
                raise
            if not data:
                self._mark_disconnected(sock)
                raise ConnectionError("TCP telemetry server closed the connection")
            buffer += data

        line, buffer = buffer.split(b"\n", 1)
        line = line.strip()
        if not line:
            return self._receive_response(sock, buffer, deadline=deadline)

        return decode_tcp_json_response(line), buffer

    def _receive_response_until(
        self,
        sock: socket.socket,
        buffer: bytes,
        deadline: float,
    ) -> tuple[dict | None, bytes]:
        """Receive one optional response without blocking the next poll send."""
        while True:
            if b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                return decode_tcp_json_response(line), buffer

            if buffer.strip():
                try:
                    response = decode_tcp_json_response(buffer.strip())
                except (ValueError, UnicodeError):
                    pass
                else:
                    return response, b""

            remaining_sec = deadline - time.monotonic()
            if remaining_sec <= 0:
                return None, buffer

            sock.settimeout(min(max(self.timeout_sec, 0.1), remaining_sec))
            try:
                data = sock.recv(self.recv_buffer_size)
            except socket.timeout:
                continue
            except OSError:
                self._mark_disconnected(sock)
                raise

            if not data:
                self._mark_disconnected(sock)
                raise ConnectionError("TCP telemetry server closed the connection")
            buffer += data
