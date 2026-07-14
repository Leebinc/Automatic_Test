import json
import socket

from src.tcp_telemetry_client import TcpTelemetryClient


def make_client(idle_disconnect_sec: float = 0.01) -> TcpTelemetryClient:
    return TcpTelemetryClient(
        host="127.0.0.1",
        port=9000,
        idle_disconnect_sec=idle_disconnect_sec,
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


def test_binary_payload_can_be_sent_on_existing_connection():
    client = make_client()
    client_sock, server_sock = socket.socketpair()

    with client_sock, server_sock:
        byte_count = client.send_payload_on_connection(
            client_sock,
            b"\x20\x00\x18\x14",
            append_newline=False,
        )

        assert byte_count == 4
        assert server_sock.recv(4096) == b"\x20\x00\x18\x14"
        assert client_sock.fileno() >= 0


def test_idle_wait_reports_server_disconnect():
    client = make_client()
    client_sock, server_sock = socket.socketpair()

    with client_sock:
        server_sock.close()
        reason = client.wait_for_idle_disconnect(client_sock)

    assert reason == "server closed the idle connection"


def test_idle_wait_reports_client_timeout():
    client = make_client(idle_disconnect_sec=0.01)
    client_sock, server_sock = socket.socketpair()

    with client_sock, server_sock:
        reason = client.wait_for_idle_disconnect(client_sock)

    assert "client idle timeout reached" in reason


def test_optional_response_wait_allows_receive_only_peer():
    client = make_client(idle_disconnect_sec=0.01)
    client_sock, server_sock = socket.socketpair()
    responses = []

    with client_sock, server_sock:
        reason, response_count = client.receive_responses_until_idle(
            client_sock,
            on_response=responses.append,
        )

    assert response_count == 0
    assert responses == []
    assert "client idle timeout reached" in reason


def test_optional_response_is_printed_and_resets_idle_wait():
    client = make_client(idle_disconnect_sec=0.01)
    client_sock, server_sock = socket.socketpair()
    responses = []

    with client_sock, server_sock:
        server_sock.sendall(b'{"code": 0, "msg": "pong"}\n')
        reason, response_count = client.receive_responses_until_idle(
            client_sock,
            on_response=responses.append,
        )

    assert response_count == 1
    assert responses == [{"code": 0, "msg": "pong"}]
    assert "client idle timeout reached" in reason


def test_optional_response_accepts_json_without_newline():
    client = make_client(idle_disconnect_sec=0.01)
    client_sock, server_sock = socket.socketpair()
    responses = []

    with client_sock, server_sock:
        server_sock.sendall(b'{"code": 0, "msg": "pong"}')
        _, response_count = client.receive_responses_until_idle(
            client_sock,
            on_response=responses.append,
        )

    assert response_count == 1
    assert responses == [{"code": 0, "msg": "pong"}]
