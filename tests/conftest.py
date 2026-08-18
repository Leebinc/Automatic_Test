import json
import platform
from pathlib import Path

import allure
import pytest
import yaml


OFFLINE_TEST_DIR = (Path(__file__).parent / "offline").resolve()


REPORT_GROUPS = {
    "test_telemetry_checks.py": (
        "半实物自动化测试",
        "遥测模式与收敛组合判据",
        allure.severity_level.CRITICAL,
    ),
    "test_tcp_telemetry_client.py": (
        "框架离线验证",
        "TCP遥测长连接",
        allure.severity_level.NORMAL,
    ),
    "test_protocols.py": (
        "框架离线验证",
        "遥测协议解析",
        allure.severity_level.NORMAL,
    ),
    "test_validators.py": (
        "框架离线验证",
        "遥测判据",
        allure.severity_level.NORMAL,
    ),
    "test_runner.py": (
        "框架离线验证",
        "测试用例编排",
        allure.severity_level.MINOR,
    ),
    "test_joint_debug.py": (
        "框架离线验证",
        "TCP联调脚本",
        allure.severity_level.MINOR,
    ),
}


@pytest.fixture(autouse=True)
def allure_report_group(request):
    """Give every test a stable hierarchy in the Allure report."""
    feature, story, severity = REPORT_GROUPS.get(
        request.node.path.name,
        ("其他测试", "未分类", allure.severity_level.NORMAL),
    )
    allure.dynamic.epic("卫星姿态控制自动化测试")
    allure.dynamic.feature(feature)
    allure.dynamic.story(story)
    allure.dynamic.severity(severity)


def pytest_collection_modifyitems(items):
    """Derive markers from the physical hardware/offline directory split."""
    for item in items:
        if Path(item.path).resolve().is_relative_to(OFFLINE_TEST_DIR):
            item.add_marker(pytest.mark.offline)
        else:
            item.add_marker(pytest.mark.hardware)


def pytest_sessionstart(session):
    """Add environment and failure-category metadata to Allure results."""
    result_dir = session.config.option.allure_report_dir
    if not result_dir:
        return

    result_path = Path(result_dir)
    result_path.mkdir(parents=True, exist_ok=True)
    env = _load_environment_config()
    properties = {
        "Python": platform.python_version(),
        "Platform": platform.platform(),
        "Telemetry TCP": _endpoint(env.get("tcp", {})),
        "Telecommand TCP": _endpoint(env.get("telecommand", {})),
        "Initial-condition UDP": _endpoint(env.get("udp", {})),
        "Telemetry poll interval": _seconds(env.get("tcp", {}).get("poll_interval_sec")),
        "Case max duration": _seconds(env.get("simulation", {}).get("max_duration_sec")),
        "Case interval": _seconds(env.get("simulation", {}).get("case_interval_sec")),
        "Judgment delay": _seconds(env.get("validation", {}).get("judgment_delay_sec")),
    }
    property_text = "\n".join(
        f"{key}={value}" for key, value in properties.items()
    )
    (result_path / "environment.properties").write_text(
        property_text + "\n",
        encoding="utf-8",
    )
    (result_path / "categories.json").write_text(
        json.dumps(_failure_categories(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_environment_config() -> dict:
    config_path = Path("config/env.yaml")
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _endpoint(config: dict) -> str:
    host = config.get("host", "unknown")
    port = config.get("port", "unknown")
    return f"{host}:{port}"


def _seconds(value) -> str:
    return "unknown" if value is None else f"{value}s"


def _degrees(value) -> str:
    return "unknown" if value is None else f"{value}deg"


def _failure_categories() -> list[dict]:
    return [
        {
            "name": "异步遥控失败",
            "matchedStatuses": ["failed"],
            "messageRegex": ".*asynchronous telecommand.*failed.*",
        },
        {
            "name": "模式工程值不匹配",
            "matchedStatuses": ["failed"],
            "messageRegex": ".*mode check .* mismatch.*",
        },
        {
            "name": "遥测判据未完成",
            "matchedStatuses": ["failed"],
            "messageRegex": ".*checks did not complete.*",
        },
        {
            "name": "通信连接异常",
            "matchedStatuses": ["broken", "failed"],
            "traceRegex": ".*(ConnectionError|TimeoutError|timed out|closed the connection).*",
        },
    ]
