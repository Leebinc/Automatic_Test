# 离线测试

本目录中的测试不连接真实的 UDP 仿真机、TCP 遥测服务器或 TCP 遥控服务器。
它们使用纯数据、Fake 客户端或本地 `socketpair` 验证协议解析、长连接、测试编排和判据逻辑。

运行全部离线测试：

```powershell
python -m pytest -m offline
```

仅运行本目录：

```powershell
python -m pytest tests/offline
```

实际硬件在环测试仍位于 `tests/test_telemetry_checks.py`，使用：

```powershell
python -m pytest -m hardware
```
