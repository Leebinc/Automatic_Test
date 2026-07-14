import json
import socket
import time


HOST = "0.0.0.0"
PORT = 9000
TOTAL_DURATION_SEC = 60.0


def build_telemetry_values(elapsed_sec: float) -> dict:
    progress = min(elapsed_sec / 30.0, 1.0)
    timestamp_ms = int(elapsed_sec * 1000)

    return {
        "ROLL": 8.0 * (1.0 - progress),
        "PITCH": -6.0 * (1.0 - progress),
        "YAW": 4.0 * (1.0 - progress),
        "WX": 1.0 * (1.0 - progress),
        "WY": -0.8 * (1.0 - progress),
        "WZ": 0.5 * (1.0 - progress),
        "MODE": "STABLE",
        "ATT_REF": "SUN",
        "TIME_MS": timestamp_ms,
    }


def build_parameter(tm_code: str, values: dict, timestamp_ms: int) -> dict | None:
    if tm_code not in values:
        return None

    return {
        "tmCode": tm_code,
        "tmName": tm_code,
        "subsystem": "simulator",
        "value": values[tm_code],
        "state": 0,
        "stateMessage": "normal",
        "source": 0,
        "valid": 2,
        "timestampMs": timestamp_ms,
        "sourceType": "simulator",
    }


def build_response(request: dict, connection_start_time: float) -> dict:
    cmd = request.get("cmd")
    elapsed_sec = min(time.monotonic() - connection_start_time, TOTAL_DURATION_SEC)
    values = build_telemetry_values(elapsed_sec)
    timestamp_ms = values["TIME_MS"]

    if cmd == "ping":
        return {"code": 0, "msg": "pong"}

    if cmd == "get":
        tm_code = request.get("tmCode")
        parameter = build_parameter(str(tm_code), values, timestamp_ms)
        if parameter is None:
            return {"code": 404, "msg": f"telemetry code not found: {tm_code}"}
        return {"code": 0, "msg": "success", "data": parameter}

    if cmd == "list":
        tm_codes = request.get("tmCodes")
        if not isinstance(tm_codes, list) or not tm_codes:
            return {"code": 400, "msg": "tmCodes must be a non-empty array"}

        parameters = []
        for tm_code in tm_codes:
            parameter = build_parameter(str(tm_code), values, timestamp_ms)
            if parameter is None:
                return {"code": 404, "msg": f"telemetry code not found: {tm_code}"}
            parameters.append(parameter)

        return {"code": 0, "msg": "success", "data": parameters}

    return {"code": 400, "msg": "cmd must be get/list/ping"}


def handle_connection(conn: socket.socket, addr) -> None:
    print(f"client connected from {addr[0]}:{addr[1]}")
    buffer = b""
    connection_start_time = time.monotonic()

    with conn:
        while True:
            data = conn.recv(4096)
            if not data:
                print(f"client disconnected from {addr[0]}:{addr[1]}")
                return

            buffer += data

            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line.decode("utf-8"))
                    response = build_response(request, connection_start_time)
                except json.JSONDecodeError:
                    response = {"code": 400, "msg": "Invalid JSON"}

                conn.sendall(
                    json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
                )


def run_server() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((HOST, PORT))
        server_socket.listen(5)

        print(f"TCP telemetry JSON simulator listening on {HOST}:{PORT}")
        print("waiting for JSON request-response connections")

        while True:
            conn, addr = server_socket.accept()
            handle_connection(conn, addr)


if __name__ == "__main__":
    run_server()
