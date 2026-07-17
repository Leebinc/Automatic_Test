import pytest

from src.runner import SimulationRunner, load_cases, load_yaml
from tests.validators import (
    assert_attitude_reference_matches,
    assert_control_mode_matches,
    has_attitude_converged_for_hold_time,
    should_check_attitude_reference,
    should_check_control_mode,
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
    control_mode_angle_threshold_deg = float(
        validation_config["control_mode_check_angle_threshold_deg"]
    )
    attitude_reference_check_delay_sec = float(
        validation_config.get("attitude_reference_check_delay_sec", 0.0)
    )
    frames = []
    converged = False
    control_mode_check_started = False
    frame_source = runner.iter_frames(sim_case)

    try:
        for frame in frame_source:
            frames.append(frame)

            check_was_started = control_mode_check_started
            control_mode_check_started = should_check_control_mode(
                frame=frame,
                threshold_deg=control_mode_angle_threshold_deg,
                check_started=control_mode_check_started,
            )
            if control_mode_check_started and not check_was_started:
                print(
                    f"{sim_case.case_id} control-mode check started; "
                    f"roll={frame.attitude_angle_deg[0]}, "
                    f"pitch={frame.attitude_angle_deg[1]}"
                )

            if control_mode_check_started:
                assert_control_mode_matches(
                    frame=frame,
                    expected_mode=sim_case.expected.control_mode,
                    case_id=sim_case.case_id,
                )

            if should_check_attitude_reference(
                frame,
                attitude_reference_check_delay_sec,
            ):
                assert_attitude_reference_matches(
                    frame=frame,
                    expected_reference=sim_case.expected.attitude_reference,
                    case_id=sim_case.case_id,
                )

            converged = has_attitude_converged_for_hold_time(
                frames=frames,
                threshold_deg=threshold,
                hold_sec=hold_sec,
            )
            if converged:
                print(
                    f"{sim_case.case_id} attitude angle converged; "
                    f"stop current case at t={frame.timestamp_sec:.3f}s"
                )
                break
    finally:
        # Closing the generator records the case end even when validation raises.
        frame_source.close()

    assert frames, f"{sim_case.case_id} did not receive any telemetry frames"

    assert converged == sim_case.expected.attitude_angle_converged, (
        f"{sim_case.case_id} attitude-angle convergence result mismatch; "
        f"expected={sim_case.expected.attitude_angle_converged}, "
        f"actual={converged}, "
        f"last_angle={frames[-1].attitude_angle_deg}, "
        f"last_rate={frames[-1].angular_rate_deg_s}"
    )
