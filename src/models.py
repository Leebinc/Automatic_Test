from dataclasses import dataclass


@dataclass
class InitialCondition:
    orbit: dict[str, float]
    year: int
    date_time: dict[str, int]
    attitude: dict[str, float]
    Spare1: dict[str, float]
    Spare2: dict[str, float]
    Config: dict


@dataclass
class ExpectedResult:
    control_mode: str
    attitude_angle_converged: bool
    attitude_reference: str


@dataclass
class SimulationCase:
    case_id: str
    description: str
    initial_condition: InitialCondition
    expected: ExpectedResult
    telecommand: object | None = None


@dataclass
class TelemetryFrame:
    case_id: str
    timestamp_sec: float
    attitude_angle_deg: list[float]
    angular_rate_deg_s: list[float] | None
    control_mode: str
    attitude_reference: str
    sim_status: str
    raw: dict


@dataclass
class TelemetryParameter:
    tm_code: str
    tm_name: str
    subsystem: str
    value: object
    state: int
    state_message: str
    source: int | None
    valid: int | None
    timestamp_ms: int | None
    source_type: str
    raw: dict
