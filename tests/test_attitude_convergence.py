import pytest

from src.runner import SimulationRunner, load_cases, load_yaml
from src.validators import final_control_mode_is, has_converged_for_hold_time


ENV_CONFIG_PATH = "config/env.yaml"
CASES_PATH = "config/cases.yaml"


@pytest.fixture(scope="session")
def env_config():
    return load_yaml(ENV_CONFIG_PATH)


@pytest.fixture(scope="session")
def runner(env_config):
    return SimulationRunner(env_config)


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


def test_angular_rate_convergence_and_control_mode(
    runner,
    validation_config,
    sim_case,
):
    frames = runner.run_once(sim_case)

    assert frames, f"{sim_case.case_id} did not receive any telemetry frames"

    threshold = float(validation_config["angular_rate_threshold_deg_s"])
    hold_sec = float(validation_config["convergence_hold_sec"])

    converged = has_converged_for_hold_time(
        frames=frames,
        threshold_deg_s=threshold,
        hold_sec=hold_sec,
    )
    mode_ok = final_control_mode_is(
        frames=frames,
        expected_mode=sim_case.expected.final_control_mode,
    )

    assert converged == sim_case.expected.angular_rate_converged, (
        f"{sim_case.case_id} angular-rate convergence result mismatch; "
        f"expected={sim_case.expected.angular_rate_converged}, "
        f"actual={converged}, "
        f"last_rate={frames[-1].angular_rate_deg_s}"
    )
    assert mode_ok, (
        f"{sim_case.case_id} control mode mismatch; "
        f"expected={sim_case.expected.final_control_mode}, "
        f"actual={frames[-1].control_mode}"
    )
