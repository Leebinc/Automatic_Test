import pytest

from src.runner import SimulationRunner, load_cases, load_yaml
from tests.validators import (
    assert_attitude_reference_matches,
    assert_control_mode_matches,
    has_attitude_converged_for_hold_time,
)


ENV_CONFIG_PATH = "config/env.yaml"
CASES_PATH = "config/cases.yaml"


@pytest.fixture(scope="session")
def env_config():
    return load_yaml(ENV_CONFIG_PATH)


@pytest.fixture(scope="session")
def runner(env_config):
    simulation_runner = SimulationRunner(env_config)
    yield simulation_runner
    simulation_runner.close()


@pytest.fixture(scope="session")
def validation_config(env_config):
    return env_config["validation"]


def pytest_generate_tests(metafunc):
    if "sim_case" in metafunc.fixturenames:
        cases = load_cases(CASES_PATH)
        metafunc.parametrize(
            "sim_case",
            cases,
            ids=[case.case_id for case in cases],
        )


def test_attitude_angle_convergence_control_mode_and_reference(
    runner,
    validation_config,
    sim_case,
):
    threshold = float(validation_config["attitude_angle_threshold_deg"])
    hold_sec = float(validation_config["convergence_hold_sec"])
    frames = []
    converged = False

    for frame in runner.iter_frames(sim_case):
        frames.append(frame)

        assert_control_mode_matches(
            frame=frame,
            expected_mode=sim_case.expected.control_mode,
            case_id=sim_case.case_id,
        )
        assert_attitude_reference_matches(
            frame=frame,
            expected_reference=sim_case.expected.attitude_reference,
            case_id=sim_case.case_id,
        )

        if not converged:
            converged = has_attitude_converged_for_hold_time(
                frames=frames,
                threshold_deg=threshold,
                hold_sec=hold_sec,
            )

    assert frames, f"{sim_case.case_id} did not receive any telemetry frames"

    assert converged == sim_case.expected.attitude_angle_converged, (
        f"{sim_case.case_id} attitude-angle convergence result mismatch; "
        f"expected={sim_case.expected.attitude_angle_converged}, "
        f"actual={converged}, "
        f"last_angle={frames[-1].attitude_angle_deg}, "
        f"last_rate={frames[-1].angular_rate_deg_s}"
    )
