import csv
import json
from dataclasses import asdict
from io import StringIO

import allure
import pytest

from src.runner import SimulationRunner, load_cases, load_yaml
from src.tcp_telemetry_client import AsyncTelecommandError
from tests.validators import (
    CheckState,
    assert_checks_completed,
    should_start_judgment,
    update_check,
)


ENV_CONFIG_PATH = "config/env.yaml"
CASES_PATH = "config/cases.yaml"


def attach_json(name: str, payload) -> None:
    allure.attach(
        json.dumps(payload, ensure_ascii=False, indent=2),
        name=name,
        attachment_type=allure.attachment_type.JSON,
    )


def attach_telemetry_trace(frames) -> None:
    if not frames:
        return

    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        (
            "timestamp_sec",
            "roll_deg",
            "pitch_deg",
            "yaw_deg",
            "control_mode",
            "attitude_reference",
            "engineering_values_json",
        )
    )
    for frame in frames:
        angles = list(frame.attitude_angle_deg) + [None, None, None]
        writer.writerow(
            (
                f"{frame.timestamp_sec:.6f}",
                *angles[:3],
                frame.control_mode,
                frame.attitude_reference,
                json.dumps(frame.values, ensure_ascii=False),
            )
        )

    allure.attach(
        output.getvalue(),
        name="遥测时序记录",
        attachment_type=allure.attachment_type.CSV,
    )
    attach_json("最后一帧原始遥测响应", frames[-1].raw)


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
    return env_config.get("validation", {})


def pytest_generate_tests(metafunc):
    if "sim_case" in metafunc.fixturenames:
        cases = load_cases(CASES_PATH)
        metafunc.parametrize(
            "sim_case",
            cases,
            ids=[case.case_id for case in cases],
        )


def test_telemetry_checks(runner, validation_config, sim_case):
    judgment_delay_sec = float(validation_config.get("judgment_delay_sec", 10.0))
    if judgment_delay_sec < 10.0:
        raise ValueError("validation.judgment_delay_sec must be at least 10 seconds")

    allure.dynamic.title(f"{sim_case.case_id}: {sim_case.description}")
    allure.dynamic.id(sim_case.case_id)
    allure.dynamic.tag("hardware-in-the-loop", "telemetry-checks")
    allure.dynamic.description(
        "所有初值和异步遥控成功发出后等待至少10秒，再按用例checks列表"
        "逐帧执行模式判别和统一收敛判别。"
    )
    allure.dynamic.parameter("判别启动延迟", f"{judgment_delay_sec}s")
    allure.dynamic.parameter("判据数量", len(sim_case.checks))

    states = [CheckState() for _ in sim_case.checks]
    frames = []
    judgment_started = False
    frame_source = runner.iter_frames(sim_case)

    with allure.step("加载测试用例和checks配置"):
        attach_json("测试用例", asdict(sim_case))
        attach_json("checks列表", sim_case.checks)

    try:
        with allure.step("发送初值与遥控，接收并实时判别遥测"):
            for frame in frame_source:
                frames.append(frame)

                if not should_start_judgment(frame, judgment_delay_sec):
                    continue
                if not judgment_started:
                    judgment_started = True
                    print(
                        f"{sim_case.case_id} telemetry judgment started at "
                        f"t={frame.timestamp_sec:.3f}s"
                    )

                for check, state in zip(sim_case.checks, states):
                    update_check(
                        frame=frame,
                        check=check,
                        state=state,
                        case_id=sim_case.case_id,
                    )

                if all(state.satisfied for state in states):
                    print(
                        f"{sim_case.case_id} all telemetry checks completed at "
                        f"t={frame.timestamp_sec:.3f}s"
                    )
                    break
    except AsyncTelecommandError as exc:
        pytest.fail(
            f"{sim_case.case_id} asynchronous telecommand failed: {exc}",
            pytrace=False,
        )
    finally:
        frame_source.close()
        attach_telemetry_trace(frames)

    with allure.step("确认所有判据完成"):
        assert frames, f"{sim_case.case_id} did not receive any telemetry frames"
        assert judgment_started, (
            f"{sim_case.case_id} did not reach judgment delay "
            f"{judgment_delay_sec}s"
        )
        assert_checks_completed(sim_case.checks, states, sim_case.case_id)
