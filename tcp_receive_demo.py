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
        telemetry_poll_codes=tcp_config["telemetry_poll_codes"],
        telemetry_frame_field_codes=tcp_config.get("telemetry_frame_field_codes"),
        poll_interval_sec=float(tcp_config.get("poll_interval_sec", 0.1)),
    )

    frames = []

    print(f"Connecting to TCP telemetry server {tcp_config['host']}:{tcp_config['port']}")

    max_duration_sec = float(env_config.get("simulation", {}).get("max_duration_sec", 10.0))

    for frame in client.receive_frames(max_duration_sec=max_duration_sec):
        frames.append(frame)

        print(
            f"received t={frame.timestamp_sec:.1f}s "
            f"rate={frame.angular_rate_deg_s} "
            f"mode={frame.control_mode}"
        )

    print(f"received total frames: {len(frames)}")
    return frames


if __name__ == "__main__":
    receive_tcp_telemetry()
