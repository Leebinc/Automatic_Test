import csv
import json
from dataclasses import asdict
from io import StringIO

import allure
import pytest

from src.runner import SimulationRunner, load_cases, load_yaml
from src.tcp_telemetry_client import AsyncTelecommandError
from tests.validators import (
    assert_attitude_reference_matches,
    assert_control_mode_matches,
    has_attitude_converged_for_hold_time,
    should_check_attitude_reference,
    should_check_control_mode,
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
            "rate_x_deg_s",
            "rate_y_deg_s",
            "rate_z_deg_s",
            "control_mode",
            "attitude_reference",
        )
    )
    for frame in frames:
        rates = frame.angular_rate_deg_s or (None, None, None)
        writer.writerow(
            (
                f"{frame.timestamp_sec:.6f}",
                *frame.attitude_angle_deg,
                *rates,
                frame.control_mode,
                frame.attitude_reference,
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
    allure.dynamic.title(f"{sim_case.case_id}: {sim_case.description}")
    allure.dynamic.id(sim_case.case_id)
    allure.dynamic.tag("hardware-in-the-loop", "attitude-control")
    allure.dynamic.description(
        "发送初始条件和八条遥控指令后轮询遥测，实时校验控制模态、"
        "姿态选择基准和三轴姿态角持续收敛。"
    )
    allure.dynamic.parameter("期望控制模态", sim_case.expected.control_mode)
    allure.dynamic.parameter("期望姿态选择基准", sim_case.expected.attitude_reference)

    threshold = float(validation_config["attitude_angle_threshold_deg"])
    hold_sec = float(validation_config["convergence_hold_sec"])
    control_mode_angle_threshold_deg = float(
        validation_config["control_mode_check_angle_threshold_deg"]
    )
    attitude_reference_check_delay_sec = float(
        validation_config.get("attitude_reference_check_delay_sec", 0.0)
    )
    allure.dynamic.parameter("三轴收敛阈值", f"±{threshold}°")
    allure.dynamic.parameter("连续收敛时间", f"{hold_sec}s")
    allure.dynamic.parameter(
        "控制模态判断启动阈值",
        f"|roll|、|pitch| < {control_mode_angle_threshold_deg}°",
    )
    allure.dynamic.parameter(
        "姿态基准判断延迟",
        f"{attitude_reference_check_delay_sec}s",
    )
    frames = []
    converged = False
    control_mode_check_started = False
    frame_source = runner.iter_frames(sim_case)

    with allure.step("加载测试用例和判据配置"):
        attach_json("测试用例", asdict(sim_case))
        attach_json("遥测判据", validation_config)

    try:
        with allure.step("执行硬件用例并实时校验遥测"):
            for frame in frame_source:
                frames.append(frame)

                check_was_started = control_mode_check_started
                control_mode_check_started = should_check_control_mode(
                    frame=frame,
                    threshold_deg=control_mode_angle_threshold_deg,
                    check_started=control_mode_check_started,
                )
                if control_mode_check_started and not check_was_started:
                    with allure.step("滚转角和俯仰角进入范围，启动控制模态判断"):
                        attach_json(
                            "控制模态判断启动帧",
                            frame.raw,
                        )
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
                    with allure.step("三轴姿态角持续满足收敛判据"):
                        attach_json("收敛确认帧", frame.raw)
                    print(
                        f"{sim_case.case_id} attitude angle converged; "
                        f"stop current case at t={frame.timestamp_sec:.3f}s"
                    )
                    break
    except AsyncTelecommandError as exc:
        pytest.fail(
            f"{sim_case.case_id} asynchronous telecommand failed: {exc}",
            pytrace=False,
        )
    finally:
        # Closing the generator records the case end even when validation raises.
        frame_source.close()
        attach_telemetry_trace(frames)

    with allure.step("确认用例最终结果"):
        assert frames, f"{sim_case.case_id} did not receive any telemetry frames"

        assert converged == sim_case.expected.attitude_angle_converged, (
            f"{sim_case.case_id} attitude-angle convergence result mismatch; "
            f"expected={sim_case.expected.attitude_angle_converged}, "
            f"actual={converged}, "
            f"last_angle={frames[-1].attitude_angle_deg}, "
            f"last_rate={frames[-1].angular_rate_deg_s}"
        )
