# 实施状态

2026-11-23。完整设计见 [实施计划](implementation-plan.md)，最新结果见 [P6 验收](p6-acceptance.md)，混合控制接口见 [P5 接口](p5-interface.md)。

| 阶段 | 状态 | 证据 |
|---|---|---|
| 计划与远程连接 | 已完成 | main，origin=https://github.com/ZZH6821/MaaTactics.git |
| P0 原生契约 | 已完成 | v6.22.0 四项入队契约；validation/p0-maacore-contract.json |
| P1 Mock MCP | 已完成并扩展 | 标准 stdio、24 Tools、Image/Resources、去重/互斥/超时/取消 |
| P2 实机闭环 | 单场景通过，发布稳定性门槛保留 | 十轮独立场景尚未完成 |
| P3 传统 Copilot | 本阶段完成 | 原格式加载/隔离验证/单文件执行/进度/错误取消及 1-7 演习 |
| P4 持久上下文与观察 | 工程验收完成，视觉自动操作门禁保留 | 原生持久/隔离/失败/取消、命名动作、绝对速度、24 帧分层指标 |
| P5 混合控制 | 工程验收完成 | 实机费用条件等待交权，Agent 部署→技能→撤退，跳过和替换后恢复，截图验证倍率及最终暂停 |
| P6 精简与提取 | Windows x64 验收完成 | `battle-v1`、Release 构建、70 项测试、24 帧固定视觉回归、1-7 演习及包内文件审计 |

本次执行至 P6，验收范围为 Windows x64。正式仓库已配置 `origin`，提交和推送尚未进行。P2 发布稳定性门槛继续单独跟踪。

## 当前验证

- Windows x64 从干净基线构建 `v6.22.0-maatactics-p6.0`。`battle-v1` 资源由 260.6 MiB 降到 53.2 MiB，完整运行时配置继续可用。
- Windows 精简 Release 通过原生冷启动和 1-7 混合控制实机演习。隔离环境加载 wheel，stdio 可发现 24 个工具。逐文件清单覆盖 Python wheel 与源码包。

- P5 普通回归通过 63 项，显式无设备原生回归通过 4 项。标准 MCP 验收包括 revision Resource、恢复时输入互斥、过期 checkpoint 和 Worker 意外退出。
- P5 补丁叠加在 P4 上。本地构建 `v6.22.0-maatactics-p5.0`，15 个补丁涉及文件均与实际构建树一致。wheel 解包验证 24 个 Schema，含混合接管、修订和恢复接口。
- P5 普通 1-7 演习中，Copilot→Agent→Copilot 聚合事件序号为 1–8，始终共用同一原生上下文。跳过的动作没有结束事件。恢复后由作业设置 2 倍速，游戏最终暂停，连接已清理。

## P4 验收基线

- 普通测试 50 项通过，另有 3 项显式原生测试通过；原生测试最后以 `-W error` 复验无警告。普通 CI 不加载 MaaCore、不连接设备。Ruff 检查和格式检查通过。
- 本地 Windows x64 Debug MaaCore 版本 v6.22.0-maatactics-p4.2；源码基线、补丁/DLL 摘要及工具链在 `native/maacore/`。
- 同进程双原生实例关卡隔离；位置、技能次数、计时跨任务保留。活跃快照 BUSY、取消失效、旧 revision 队列拒绝、false 失败事件均通过。
- 标准 MCP 实机演习完成：倍率 1→2→2→1（重复 2 不点击）；凛御银灰 [4,3] 按名称部署→技能→撤退，原生动作序号连续 3/4/5。
- 暂停/继续均由 MCP 执行并视觉核验。受限演习启动入口独立实测成功并暂停；跨战斗上下文重置/epoch 更新另有 Mock 回归，未冒充额外实机轮次。
- 普通战术继续返回 game_effect=unknown，截图由外部 Agent 核验。引擎位置为 estimated；Ready 为 experimental、confidence=null。
- 24 帧固定原图覆盖普通/暂停、动画/特效遮挡、默认皮肤、撤退及非战斗界面；分字段和分层指标在 `validation/p4-vision-benchmark.json`。
- 本样本费用、击杀、卡片数量和费用匹配；倍率召回 78.3%、unknown 25%，暂停状态值召回 95.7%。无缓存名字 unknown 100%。这些小样本比例不外推。
- Ready 只有 4 张已知位置样本（1 正、3 负），没有替代皮肤覆盖；自动 Ready 继续关闭。视觉干员盘点、方向和可靠游戏阶段识别尚未提供。
- 显式断开 closed；独立只读 ADB 截图复核游戏仍暂停。新 wheel 解包导入检查通过：21 个 Schema、名称目标、启动 60 秒上限、LICENSE/第三方声明，未包含 DLL/权重。

## 已处理问题

- SingleStep 静态关卡及短生命期战斗对象改为实例级持久上下文。
- 零延迟策略跳过仅作用于 TacticalBattleTask，传统 Copilot 保持原行为；ResetStopwatch 补全成功返回。
- Debug 改用 /MDd 的 zsd.lib，消除 LNK4098，未压制警告。
- 冷模型初始化曾使首张截图超过 10 秒；现为有界 30 秒，保留真实采集时间，过旧图片仍拒绝用于动作。
- 新战斗清空旧位置/意图；失败、停止和取消后重新绑定/观察，不猜测恢复。
- Resource 过期有结构化错误；测试客户端保留终态、清除失败后的旧回执，断开只发送规定参数。
- 补丁新增文件标记已修正；全新基线正向应用后 13 个文件逐字节匹配构建源码。

## 后续门槛

P2 十轮独立实机场景、替代皮肤/更多干员地图数据、Ready 校准、自动编队成功路径及原生包资源分发许可审计仍保留。当前平台范围为 Windows x64，成果为本地开发版本。
MuMu 键位已调研；尚无游戏帧号反馈，`precise_frame_step=false`，开局暂停不承诺第零帧。见 [调研记录](mumu-keymap-research.md)。
