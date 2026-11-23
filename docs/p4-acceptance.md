# P4 工程验收记录

2026-11-17，本地 Windows x64 Debug，普通 1-7 演习。本记录对应原计划 P4，
保留其“数据不足不开放自动执行依赖”的条件；不将 P5 或 P2 十轮稳定性记为完成。

## 要求与证据

| 要求 | 实现与证据 | 结论 |
|---|---|---|
| 实例级持久上下文 | Assistant::tactical_battle → TacticalBattleTask；双原生句柄关卡/位置隔离 | 通过 |
| 保留位置、技能、计时 | SetUnitLocation / SkillUsage / ResetStopwatch / Output 独立任务；native-contract | 通过 |
| 保留镜头映射 | 同一个 BattleHelper 和 Tile 信息贯穿任务；camera_shift/count 快照及只读前后不变 | 通过；非零镜头实景未认证 |
| 序号、结果和原因事件 | TacticalActionCompleted；实机序号 1–5；false 返回失败终态 10000 | 通过 |
| 只读观察不点击未知干员 | 直接 Matcher/Classifier，只匹配已有头像缓存；探针 input_issued=false | 通过 |
| 暂停/继续和绝对倍率 | MCP 实机前后图；1→2→2→1，重复目标不点击 | 通过 |
| 连续命名部署/技能/撤退 | 同一 revision=3，序号 3/4/5；原图独立审查 | 通过 |
| 并发、失效、取消 | 活跃快照 BUSY、第二实例可读、活跃 Reset 拒绝、取消/旧队列失效 | 通过 |
| Worker 生命周期 | 独立进程/急停通道、超时回收测试、真实断开 closed、关闭后只读截图 | 通过 |
| 固定截图/前处理 | 24 个 SHA 绑定私有原图；分类器源码与冻结上游相同 | 通过 |
| 动画/遮挡/皮肤分层 | 标签和逐层指标已生成；皮肤层当前只有默认皮肤 | 有限覆盖；自动 Ready 门禁关闭 |
| 分字段指标 | precision、recall、unknown、延迟；无缓存名字拒识；Ready 正类混淆矩阵 | 通过，不外推准确率 |
| 无设备 CI/旧版兼容 | 50 普通 + 3 显式原生测试；原版 v6.22.0 契约和 Copilot 解析仍通过 | 通过 |
| 可维护补丁 | 13 文件正向应用后字节一致、反向及格式检查、无 CRT 冲突 | 通过 |

镜头状态由持久对象所有权和字段快照验证；当前 MCP 不提供 MoveCamera，
没有在 1-7 伪造非零镜头操作或宣称多波转场已经实测。
同进程双句柄更直接检查了旧静态关卡问题；服务仍只允许一个活跃设备会话，
不同时运行两个游戏控制器。新版启动重置/epoch 更新有 Mock 回归，实机启动证据在该修复之前。

## 视觉指标

precision/recall 指已知字段值的精确匹配检索，错误非空值同时记误报/漏报；
unknown 对已知真值记漏报。unknown 比例含非战斗界面的正确拒识。
延迟为热身后本机 Debug 字段耗时中位数。

| 字段 | 样本 | precision | recall | unknown | 中位 ms |
|---|---:|---:|---:|---:|---:|
| cost | 24 | 100% | 100% | 4.2% | 82 |
| kills / total_kills | 各 24 | 100% | 100% | 4.2% | 154 |
| battle_speed | 24 | 100% | 78.3% | 25% | 50 |
| paused 状态值 | 24 | 100% | 95.7% | 8.3% | 20 |
| 卡片数量 / 卡片费用 | 各 24 | 100% | 100% | 4.2% | 111 |
| 无缓存卡片名字 | 10 | 无预测，不定义 | 0% | 100% | 112 |
| Ready 候选 | 4 | 100% | 100% | 0% | 63 |

Ready 正类 TP=1、FP=0、FN=0、TN=3。位置由人工标签植入内存，
验证的是已知位置分类，不是检测部署位置。样本高度相关，没有替代皮肤、多干员/地图、
可关闭技能 c 类覆盖，自动 Ready 保持关闭。所有 100% 只描述本小样本。
红色警报 HUD 导致 1 张暂停 unknown；倍率阈值不足导致 5 张 unknown，保留拒识。
两个击杀标签从小联系图误读，经完整原图复核更正，修订原因保留在标签清单。
只读空名字并不冒充可用干员身份识别；部署位置继续标为引擎估计。

## 复验与记录

- [原生契约](validation/p4-native-contract.json)
- [实机动作](validation/p4-device-scenario.json)
- [视觉报告](validation/p4-vision-benchmark.json)
- [补丁复现](validation/p4-patch-check.json)
- `tests/fixtures/vision/p4-private-frames.json`：原图路径、SHA、标签、修订与覆盖。
- `artifacts/p4-real-actions/`、`artifacts/p4-cleanup/`：本机原图/日志，Git 忽略。
- `native/maacore/manifest.json`：基线/补丁/构建摘要，不分发原生二进制。

```powershell
uv run pytest -m 'not native and not device'
$env:MAATACTICS_NATIVE_RUNTIME = 'C:\path\to\MAA'
$env:MAATACTICS_P4_NATIVE_RUNTIME = (Resolve-Path '../.build/maacore-build/bin/Debug').Path
$env:MAATACTICS_P4_RESOURCE_ROOT = $env:MAATACTICS_NATIVE_RUNTIME
uv run pytest -m native -W error
uv run python tools/benchmark_vision.py --runtime-root $env:MAATACTICS_P4_NATIVE_RUNTIME `
  --resource-root $env:MAATACTICS_P4_RESOURCE_ROOT
```

这些测试不连接设备；benchmark 还依赖本机私有原图，原图未获随项目分发授权。
真实设备脚本只能显式手动启用。跨皮肤数据扩充、P2 十轮稳定性、原生发布包和
Release/跨平台认证仍保留，不会被一次成功或 Mock 结果自动解除。
