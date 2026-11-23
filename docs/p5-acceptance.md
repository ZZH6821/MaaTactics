# P5 混合控制验收

2026-11-18，Windows x64 Debug，MuMu ADB，普通 1-7 演习。通过标准 MCP stdio 客户端运行，原生识别到 `BattleStartExercise` 后启动，战斗截图显示「演习中」。

| 计划要求 | 验收证据 |
|---|---|
| 动作边界交权 | 未连接设备的 Debug 演练在执行第 0 个动作之前暂停并恢复。 |
| 条件等待交权 | 实机在动作 2 的 `cost_changes` 等待处接管。费用基准 14 在接管及修订期间保留。另用原生演练检查计时条件。 |
| 停止作业操作 | 实机 checkpoint 中的策略轮询计数在 Agent 三个动作前后始终为 3。原生计时演练检查停转和时钟冻结。 |
| 保留上下文 | 实机所有八个结束事件共用一个 context revision。原生内存演练检查分组、位置、技能策略和计时。镜头状态随同一个 BattleHelper 保存。 |
| Agent 连续动作 | 按名称部署凛御银灰至 [4,3]，释放技能并撤退。独立检查操作前后原图，确认位置、技能效果和再部署冷却。 |
| 修订及恢复 | 第 2 项替换等待，第 3 项跳过，第 4 项替换为倍率动作。revision 2 可通过 Resource 读取。原作业实际完成索引为 0、1、2、4、5，恢复后的截图显示 2X。 |
| 单一控制者 | 原生事件序列 1–2 为 Copilot，3–5 为 Agent，6–8 为 Copilot。回归覆盖恢复期间拒绝并发 Agent 输入、迟到接管及重复请求。 |
| 异常路径 | 测试涵盖回滚不了的原生取消、暂停时断开、恢复期间 Worker 退出、过期 lease、HUD 失配和 context revision 失配。 |
| 停止与清理 | 成功轮次由 MCP 确认最终暂停并完成断开。断开后只读 ADB 截图确认游戏暂停。首轮脚本参数冲突触发了显式停止和断开，修正后完整复跑。 |
| 兼容与构建 | P4 及 P5 补丁可顺序应用，15 个文件与已构建源码一致。原版 v6.22.0 与补丁 ABI 无设备回归均通过。 |

真实动作结束事件只记录引擎返回值，截图提供游戏效果证据。普通 1-7 演习验证了接管链路。多地图、非零镜头实景、精确过帧与 P2 十轮发布门槛仍单独跟踪，Agent Ready 自动执行继续关闭。

- [实机验收原生事件与截图摘要](validation/p5-device-scenario.json)
- [原生无设备演练](validation/p5-native-contract.json)
- [补丁复现记录](validation/p5-patch-check.json)
- [混合控制接口](p5-interface.md)

原图和设备日志保存在 `artifacts/p5-device-verified` 与 `artifacts/p5-device-final`。手工复验通过 `tools/verify_hybrid_device.py`，执行前进入本机关卡的演习编队，并传 `--confirmed-exercise-formation`。普通 CI 不启动设备。
