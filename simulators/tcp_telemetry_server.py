import json
import socket
import time


HOST = "0.0.0.0"
PORT = 9000
TOTAL_DURATION_SEC = 60.0
SEND_INTERVAL_SEC = 0.1

SCENARIOS = [
    {
        "case_id": "case_001_udp_demo",
        "name": "converged",
        "should_converge": True,
    },
    {
        "case_id": "case_002_not_converged",
        "name": "not_converged",
        "should_converge": False,
    },
]


def build_telemetry_frame(timestamp_sec: float, scenario: dict) -> dict:
    if scenario["should_converge"]:
        progress = min(timestamp_sec / 30.0, 1.0)
        wx = 1.0 * (1.0 - progress)
        wy = -0.8 * (1.0 - progress)
        wz = 0.5 * (1.0 - progress)
    else:
        progress = min(timestamp_sec / TOTAL_DURATION_SEC, 1.0)
        wx = 1.2 - 0.4 * progress
        wy = -1.0 + 0.3 * progress
        wz = 0.8 - 0.2 * progress

    control_mode = "DETUMBLE" if timestamp_sec < 15.0 else "STABLE"
    sim_status = "FINISHED" if timestamp_sec >= TOTAL_DURATION_SEC else "RUNNING"

    return {
        "case_id": scenario["case_id"],
        "timestamp_sec": round(timestamp_sec, 3),
        "angular_rate_deg_s": [round(wx, 6), round(wy, 6), round(wz, 6)],
        "control_mode": control_mode,
        "sim_status": sim_status,
    }


def send_one_simulation(conn: socket.socket, scenario: dict) -> None:
    frame_count = int(TOTAL_DURATION_SEC / SEND_INTERVAL_SEC) + 1

    for index in range(frame_count):
        timestamp_sec = min(index * SEND_INTERVAL_SEC, TOTAL_DURATION_SEC)
        frame = build_telemetry_frame(timestamp_sec, scenario)
        line = json.dumps(frame).encode("utf-8") + b"\n"
        conn.sendall(line)

        print(
            "sent "
            f"case={frame['case_id']} "
            f"t={frame['timestamp_sec']:.1f}s "
            f"rate={frame['angular_rate_deg_s']} "
            f"mode={frame['control_mode']} "
            f"status={frame['sim_status']}"
        )

        if frame["sim_status"] == "FINISHED":
            break

        time.sleep(SEND_INTERVAL_SEC)


def run_server() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((HOST, PORT))
        server_socket.listen(1)

        print(f"TCP telemetry simulator listening on {HOST}:{PORT}")
        print(f"waiting for {len(SCENARIOS)} simulation connection(s)")

        for scenario_index, scenario in enumerate(SCENARIOS, start=1):
            conn, addr = server_socket.accept()

            with conn:
                print(
                    f"client connected from {addr[0]}:{addr[1]}, "
                    f"scenario {scenario_index}/{len(SCENARIOS)}: "
                    f"{scenario['name']}"
                )
                send_one_simulation(conn, scenario)

        print("TCP telemetry simulator finished all scenarios")


if __name__ == "__main__":
    run_server()
