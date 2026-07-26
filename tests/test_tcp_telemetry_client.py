import json
import socket
import threading

import pytest

from src.tcp_telemetry_client import AsyncTelecommandError, TcpTelemetryClient


def make_client() -> TcpTelemetryClient:
    return TcpTelemetryClient(
        host="127.0.0.1",
        port=9000,
        timeout_sec=0.01,
    )


def test_request_on_connection_keeps_socket_open():
    client = make_client()
    client_sock, server_sock = socket.socketpair()

    with client_sock, server_sock:
        server_sock.sendall(b'{"code": 0, "msg": "pong"}\n')
        response, buffer = client.request_on_connection(
            client_sock,
            {"cmd": "ping"},
        )

        request = json.loads(server_sock.recv(4096).decode("utf-8"))
        assert request == {"cmd": "ping"}
        assert response == {"code": 0, "msg": "pong"}
        assert buffer == b""
        assert client_sock.fileno() >= 0


def test_persistent_connection_is_reused_until_explicit_close():
    client = make_client()
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock

    try:
        assert client.open_connection() is client_sock
        assert client.open_connection() is client_sock
        assert client_sock.fileno() >= 0

        client.close()

        assert client_sock.fileno() == -1
    finally:
        server_sock.close()


def test_multiple_requests_share_one_persistent_connection():
    client = make_client()
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock

    try:
        server_sock.sendall(
            b'{"code": 0, "msg": "pong-1"}\n'
            b'{"code": 0, "msg": "pong-2"}\n'
        )

        first = client.request_once({"cmd": "ping", "index": 1})
        second = client.request_once({"cmd": "ping", "index": 2})
        requests = [
            json.loads(line)
            for line in server_sock.recv(4096).decode("utf-8").splitlines()
        ]

        assert first == {"code": 0, "msg": "pong-1"}
        assert second == {"code": 0, "msg": "pong-2"}
        assert requests == [
            {"cmd": "ping", "index": 1},
            {"cmd": "ping", "index": 2},
        ]
        assert client.open_connection() is client_sock
    finally:
        client.close()
        server_sock.close()


def test_async_notify_and_execute_use_command_code_and_matching_request_ids():
    client = make_client()
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock

    try:
        server_sock.sendall(
            b'{"requestId":"notify-1","code":0,"msg":"Channel notified"}\n'
        )
        notification = client.send_async_telecommand_request(
            operation="notify",
            command_code="K3036",
            satellite="SAT-1",
            channel="CH1",
            response_timeout_sec=1.0,
            request_id="notify-1",
        )
        notify_request = json.loads(server_sock.recv(4096).decode("utf-8"))

        server_sock.sendall(
            b'{"requestId":"execute-1","code":0,'
            b'"msg":"Command executed successfully"}\n'
        )
        execution = client.send_async_telecommand_request(
            operation="execute",
            command_code="K3036",
            satellite="SAT-1",
            channel="CH1",
            response_timeout_sec=1.0,
            request_id="execute-1",
        )
        execute_request = json.loads(server_sock.recv(4096).decode("utf-8"))

        assert notify_request == {
            "cmd": "notify",
            "commandCode": "K3036",
            "requestId": "notify-1",
            "satellite": "SAT-1",
            "channel": "CH1",
        }
        assert execute_request == {
            "cmd": "execute",
            "commandCode": "K3036",
            "requestId": "execute-1",
            "satellite": "SAT-1",
            "channel": "CH1",
        }
        assert notification["code"] == 0
        assert execution["code"] == 0
        assert client.open_connection() is client_sock
    finally:
        client.close()
        server_sock.close()


def test_async_nonzero_result_raises_and_keeps_connection_open():
    client = make_client()
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock

    try:
        server_sock.sendall(
            b'{"requestId":"execute-failed","code":-1,'
            b'"msg":"execution failed"}\n'
        )

        with pytest.raises(AsyncTelecommandError, match="execute failed"):
            client.send_async_telecommand_request(
                operation="execute",
                command_code="K3036",
                response_timeout_sec=1.0,
                request_id="execute-failed",
            )

        assert client.open_connection() is client_sock
    finally:
        client.close()
        server_sock.close()


def test_async_out_of_order_response_is_saved_for_matching_request():
    client = make_client()
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock

    try:
        server_sock.sendall(
            b'{"requestId":"execute-later","code":0,'
            b'"msg":"Command executed successfully"}\n'
            b'{"requestId":"notify-now","code":0,"msg":"Channel notified"}\n'
        )
        notification = client.send_async_telecommand_request(
            operation="notify",
            command_code="K3036",
            response_timeout_sec=1.0,
            request_id="notify-now",
        )
        execute_response = client.send_async_telecommand_request(
            operation="execute",
            command_code="K3036",
            response_timeout_sec=1.0,
            request_id="execute-later",
        )
        sent_requests = [
            json.loads(line)
            for line in server_sock.recv(4096).decode("utf-8").splitlines()
        ]

        assert notification["requestId"] == "notify-now"
        assert execute_response["requestId"] == "execute-later"
        assert [request["cmd"] for request in sent_requests] == [
            "notify",
            "execute",
        ]
    finally:
        client.close()
        server_sock.close()


def test_polling_resends_requests_when_server_does_not_respond():
    client = TcpTelemetryClient(
        host="127.0.0.1",
        port=9000,
        timeout_sec=0.01,
        poll_interval_sec=0.01,
        telemetry_poll_codes=["ROLL", "PITCH", "YAW", "MODE", "ATT_REF"],
    )
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock

    try:
        frames = list(client.receive_frames(max_duration_sec=0.06))
        requests = server_sock.recv(4096).decode("utf-8").splitlines()

        assert frames == []
        assert len(requests) >= 3
        assert all(json.loads(item)["cmd"] == "list" for item in requests)
        assert client.open_connection() is client_sock
    finally:
        client.close()
        server_sock.close()


def test_receive_only_peer_does_not_trigger_client_idle_disconnect():
    client = make_client()
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock
    responses = []
    close_timer = threading.Timer(0.03, server_sock.close)
    close_timer.start()

    try:
        reason, response_count = client.receive_responses_until_closed(
            client_sock,
            on_response=responses.append,
        )
    finally:
        close_timer.join()
        client.close()

    assert response_count == 0
    assert responses == []
    assert reason == "server closed the connection"


def test_optional_response_is_received_before_server_close():
    client = make_client()
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock
    responses = []

    try:
        server_sock.sendall(b'{"code": 0, "msg": "pong"}\n')
        server_sock.close()
        reason, response_count = client.receive_responses_until_closed(
            client_sock,
            on_response=responses.append,
        )
    finally:
        client.close()

    assert response_count == 1
    assert responses == [{"code": 0, "msg": "pong"}]
    assert reason == "server closed the connection"


def test_optional_response_accepts_json_without_newline():
    client = make_client()
    client_sock, server_sock = socket.socketpair()
    client._socket = client_sock
    responses = []

    try:
        server_sock.sendall(b'{"code": 0, "msg": "pong"}')
        server_sock.close()
        _, response_count = client.receive_responses_until_closed(
            client_sock,
            on_response=responses.append,
        )
    finally:
        client.close()

    assert response_count == 1
    assert responses == [{"code": 0, "msg": "pong"}]
