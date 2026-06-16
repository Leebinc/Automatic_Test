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
    final_control_mode: str
    angular_rate_converged: bool


@dataclass
class SimulationCase:
    case_id: str
    description: str
    initial_condition: InitialCondition
    expected: ExpectedResult


@dataclass
class TelemetryFrame:
    case_id: str
    timestamp_sec: float
    angular_rate_deg_s: list[float]
    control_mode: str
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
