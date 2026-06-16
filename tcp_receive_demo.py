from src.runner import load_yaml
from src.tcp_telemetry_client import TcpTelemetryClient


ENV_CONFIG_PATH = "config/env.yaml"


def receive_tcp_telemetry() -> list:
    env_config = load_yaml(ENV_CONFIG_PATH)
    tcp_config = env_config["tcp"]

    client = TcpTelemetryClient(
        host=tcp_config["host"],
        port=int(tcp_config["port"]),
        timeout_sec=float(tcp_config.get("timeout_sec", 2.0)),
        recv_buffer_size=int(tcp_config.get("recv_buffer_size", 4096)),
    )

    frames = []

    print(f"Connecting to TCP telemetry server {tcp_config['host']}:{tcp_config['port']}")

    for frame in client.receive_frames():
        frames.append(frame)

        print(
            f"received t={frame.timestamp_sec:.1f}s "
            f"rate={frame.angular_rate_deg_s} "
            f"mode={frame.control_mode} "
            f"status={frame.sim_status}"
        )

        if frame.sim_status == "FINISHED":
            break

    print(f"received total frames: {len(frames)}")
    return frames


if __name__ == "__main__":
    receive_tcp_telemetry()
