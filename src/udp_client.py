import socket

from src.models import InitialCondition
from src.protocols import encode_initial_condition


class UdpInitialConditionClient:
    def __init__(self, host: str, port: int, timeout_sec: float = 2.0):
        self.host = host
        self.port = port
        self.timeout_sec = timeout_sec

    def send_initial_condition(self, condition: InitialCondition) -> bytes:
        """Send one binary initial-condition frame by UDP."""
        payload = encode_initial_condition(condition)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.settimeout(self.timeout_sec)
            udp_socket.sendto(payload, (self.host, self.port))

        return payload
