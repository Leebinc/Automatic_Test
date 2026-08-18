import socket
import time
import uuid
from collections.abc import Iterator
from threading import Event

from src.models import TelemetryFrame
from src.protocols import (
    decode_tcp_json_response,
    decode_telemetry_response_frame,
    decode_telemetry_response_parameters,
    encode_tcp_json_command,
)


#定义异步遥控异常，是RuntimeError的类继承
class AsyncTelecommandError(RuntimeError):
    """Raised when an asynchronous notify/execute request does not succeed."""


#TCP客户端（适用于遥控和遥测）
class TcpTelemetryClient:
    def __init__(
        self,
        host: str,
        port: int,
        timeout_sec: float = 2.0,
        recv_buffer_size: int = 4096,
        connect_retry_timeout_sec: float = 20.0,
        connect_retry_interval_sec: float = 5.0,
        telemetry_poll_codes: list[str] | None = None,
        telemetry_frame_field_codes: dict | None = None,
        poll_interval_sec: float = 1.0,
        heartbeat_interval_sec: float | None = None,
    ):
        self.host = host                                                             #客户端IP
        self.port = port                                                             #客户端端口号
        self.timeout_sec = timeout_sec                                               #超时设置，默认2.0s
        self.recv_buffer_size = recv_buffer_size                                     #处理TCP粘包的缓冲区大小，默认4KB
        self.connect_retry_timeout_sec = connect_retry_timeout_sec                   #最大连接时长，默认20.0s
        self.connect_retry_interval_sec = connect_retry_interval_sec                 #单次连接超时后重新连接设置，默认5.0s
        self.telemetry_poll_codes = telemetry_poll_codes or []                       #遥测轮询代号列表（协议）
        self.telemetry_frame_field_codes = telemetry_frame_field_codes or {}         #遥测框架字典（自设）
        self.poll_interval_sec = poll_interval_sec                                   #遥测轮询间隔时长，默认1.0s
        self.heartbeat_interval_sec = heartbeat_interval_sec                         #TCP连接心跳响应间隔，默认为None
        self._socket: socket.socket | None = None                                    #持久TCP连接，默认为None
        self._receive_buffer = b""                                                   #未解析完的TCP数据
        self._pending_async_responses: dict[str, dict] = {}                          #提前收到或不属于当前请求的异步遥控响应


    #创建TCP连接, 在限定时间内重试
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
                remaining = deadline - time.monotonic()
                time.sleep(min(self.connect_retry_interval_sec, max(remaining, 0)))

        raise TimeoutError(
            f"could not connect to TCP telemetry server "
            f"{self.host}:{self.port} within "
            f"{self.connect_retry_timeout_sec}s"
        ) from last_error


    #发送心跳响应请求命令并等待回应
    def ping(self) -> dict:
        return self.request_once({"cmd": "ping"})

    #请求单条遥测
    def get_parameter(self, tm_code: str):
        response = self.request_once({"cmd": "get", "tmCode": tm_code})
        parameters = decode_telemetry_response_parameters(response)
        return parameters[0] if parameters else None


    #请求多条遥测
    def list_parameters(self, tm_codes: list[str]):
        response = self.request_once({"cmd": "list", "tmCodes": tm_codes})
        return decode_telemetry_response_parameters(response)


    #发送notify或execute遥控请求, 并等待对应结果
    def send_async_telecommand_request(
        self,
        operation: str,
        command_code: str,
        response_timeout_sec: float,
        satellite: str | None = None,
        channel: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        """Send notify/execute and wait for its matching asynchronous result."""
        operation = str(operation).strip().lower()
        if operation not in {"notify", "execute"}:
            raise ValueError(
                f"asynchronous telecommand operation must be notify or execute: "
                f"{operation!r}"
            )

        command_code = str(command_code).strip()
        if not command_code:
            raise ValueError("telecommand command_code must not be empty")

        response_timeout_sec = float(response_timeout_sec)
        if response_timeout_sec <= 0:
            raise ValueError("asynchronous response timeout must be greater than zero")

        request_id = str(request_id or uuid.uuid4())
        command = {
            "cmd": operation,
            "commandCode": command_code,
            "requestId": request_id,
        }
        if satellite:
            command["satellite"] = str(satellite)
        if channel:
            command["channel"] = str(channel)

        try:
            sock = self.open_connection()
            self._send_command(sock, command)
            deadline = time.monotonic() + response_timeout_sec
            response = self._wait_for_async_response(
                sock=sock,
                request_id=request_id,
                deadline=deadline,
            )
        except AsyncTelecommandError:
            raise
        except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
            raise AsyncTelecommandError(
                f"asynchronous telecommand {operation} did not complete; "
                f"command_code={command_code}, request_id={request_id}, "
                f"reason={exc}"
            ) from exc

        try:
            response_code = int(response.get("code", -1))
        except (TypeError, ValueError) as exc:
            raise AsyncTelecommandError(
                f"asynchronous telecommand {operation} returned an invalid code; "
                f"command_code={command_code}, request_id={request_id}, "
                f"response={response}"
            ) from exc

        if response_code != 0:
            raise AsyncTelecommandError(
                f"asynchronous telecommand {operation} failed; "
                f"command_code={command_code}, request_id={request_id}, "
                f"code={response_code}, msg={response.get('msg', '')}"
            )

        return response

    #等待指定requestID结果, 若不是期望requestID则存储在self._pending_async_responses
    def _wait_for_async_response(
        self,
        sock: socket.socket,
        request_id: str,
        deadline: float,
    ) -> dict:
        pending = self._pending_async_responses.pop(request_id, None)
        if pending is not None:
            return pending

        while True:
            response, self._receive_buffer = self._receive_response(
                sock,
                self._receive_buffer,
                deadline=deadline,
            )
            response_request_id = response.get("requestId")
            if response_request_id is None:
                raise AsyncTelecommandError(
                    f"asynchronous response is missing requestId: {response}"
                )

            response_request_id = str(response_request_id)
            if response_request_id == request_id:
                return response

            self._pending_async_responses[response_request_id] = response

    #发送命令——等待响应, 使用类自己的socket
    def request_once(self, command: dict) -> dict:
        sock = self.open_connection()
        self._send_command(sock, command)
        response, self._receive_buffer = self._receive_response(
            sock,
            self._receive_buffer,
        )
        return response


    #返回当前持久连接
    def open_connection(self) -> socket.socket:
        """Return the process-level persistent TCP connection."""
        if self._socket is None or self._socket.fileno() < 0:
            self._socket = self._connect()
            self._receive_buffer = b""
        return self._socket

    #主动关闭客户端
    def close(self) -> None:
        """Close the persistent connection when the client process is ending."""
        sock = self._socket
        self._socket = None
        self._receive_buffer = b""
        self._pending_async_responses.clear()
        if sock is None:
            return

        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            sock.close()

    #使用调用方提供的socket, socket可由外部传入
    def request_on_connection(
        self,
        sock: socket.socket,
        command: dict,
        buffer: bytes = b"",
    ) -> tuple[dict, bytes]:
        """Send one JSON command without closing the existing connection."""
        self._send_command(sock, command)
        return self._receive_response(sock, buffer)

    #发送命令，不等待响应
    def send_command_on_connection(
        self,
        sock: socket.socket,
        command: dict,
    ) -> None:
        """Send one JSON command without requiring a response."""
        self._send_command(sock, command)

    #持续从socket接收Json响应, 直至stop_event被设置(ctrl+c)/服务端关闭连接/socket出现错误
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

    #保持连接并等待服务器断开
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

    #从缓冲区解析尽可能多的消息
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

    #socket出错或关闭时清理内部状态
    def _mark_disconnected(self, sock: socket.socket) -> None:
        if sock is not self._socket:
            return
        try:
            sock.close()
        finally:
            self._socket = None
            self._receive_buffer = b""
            self._pending_async_responses.clear()


    #遥测帧轮询
    def receive_frames(
        self,
        max_duration_sec: float | None = None,
        stop_event: Event | None = None,
    ) -> Iterator[TelemetryFrame]:
        if not self.telemetry_poll_codes:
            raise ValueError(
                "tcp.telemetry_poll_codes must be configured for the JSON "
                "request-response telemetry protocol"
            )

        yield from self._poll_frames(
            max_duration_sec=max_duration_sec,
            stop_event=stop_event,
        )

    #遥测轮询
    def _poll_frames(
        self,
        max_duration_sec: float | None = None,
        stop_event: Event | None = None,
    ) -> Iterator[TelemetryFrame]:
        command = {"cmd": "list", "tmCodes": self.telemetry_poll_codes}
        if stop_event is not None and stop_event.is_set():
            return

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
            if stop_event is not None and stop_event.is_set():
                return
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
            if stop_event is not None:
                receive_deadline = min(receive_deadline, now + 0.5)

            response, self._receive_buffer = self._receive_response_until(
                sock,
                self._receive_buffer,
                deadline=receive_deadline,
            )
            if response is None:
                continue

            if self._handle_heartbeat_response(response):
                continue

            frame = decode_telemetry_response_frame(
                response=response,
                field_codes=self.telemetry_frame_field_codes,
            )
            # Convergence duration uses local monotonic elapsed time, not
            # spacecraft/system telemetry time.
            frame.timestamp_sec = time.monotonic() - start_time
            yield frame

    #返回心跳是否到期
    def _heartbeat_due(self, now: float, last_heartbeat_time: float) -> bool:
        return (
            self.heartbeat_interval_sec is not None
            and self.heartbeat_interval_sec > 0
            and now - last_heartbeat_time >= self.heartbeat_interval_sec
        )

    #识别并校验心跳响应；非心跳返回False，合法心跳返回True，非法心跳抛错
    def _handle_heartbeat_response(self, response: dict) -> bool:
        if str(response.get("msg", "")).strip().lower() != "pong":
            return False

        try:
            code = int(response.get("code", -1))
        except (TypeError, ValueError) as exc:
            raise ConnectionError(
                f"TCP telemetry heartbeat returned an invalid code: {response!r}"
            ) from exc

        if code != 0:
            raise ConnectionError(
                f"TCP telemetry heartbeat failed: "
                f"code={code}, msg={response.get('msg', '')}"
            )

        data = response.get("data")
        if data not in (None, [], {}):
            raise ConnectionError(
                f"TCP telemetry heartbeat returned unexpected data: {data!r}"
            )
        return True

    #底层命令发送
    def _send_command(self, sock: socket.socket, command: dict) -> None:
        try:
            sock.sendall(encode_tcp_json_command(command))
        except OSError:
            self._mark_disconnected(sock)
            raise

    #阻塞式响应接收，保证返回一条完整的换行分隔 JSON 响应
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
                    raise TimeoutError("TCP response operation deadline reached")
                sock.settimeout(min(self.timeout_sec, remaining_sec))
            else:
                sock.settimeout(self.timeout_sec)

            try:
                data = sock.recv(self.recv_buffer_size)
            except socket.timeout:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("TCP response operation deadline reached")
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

    #有截止时间的可选接收，不一定收到响应
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
