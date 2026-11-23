# P4 持久上下文与观察接口

完整输入契约由 `input_schemas()` 展开 `src/maatactics/tool-input-schemas.json` 生成，
通过 MCP `tools/list` 发现。当前共 21 个工具；保留 P3 原格式 Copilot 接口。
业务层不依赖 MCP，设备输入仍由一个 Worker 串行执行。

## 运行时与会话

P4 使用 `native/maacore/manifest.json` 对应的补丁 MaaCore，扩展 ABI v1。
原版 v6.22.0 继续支持旧入口，但不声称具有持久上下文、按名称技能/撤退或结构化观察。
配置中的 `runtime_root` 指向 DLL 目录，`resource_root` 指向外置 MAA 安装根目录；
`expected_sha256` 必须匹配实际 DLL。模型、GUI 和游戏资源不随项目分发。

`set_stage("main_01-07")` 绑定普通 1-7 并返回新 epoch。部署名、坐标、技能设置、
镜头映射和计时由同一个原生上下文持有；每条独立 SingleStep 捕获绑定 revision。
失败、取消、停止、换图、完整 Copilot 执行均使旧上下文失效。重新绑定后必须重新观察。
`start_battle` 真正启动新演习时也重置原生上下文，清空意图并返回新的 `result.epoch`；
已经在战斗内的幂等调用不重置。客户端从结果或 `get_session_status` 获取当前 epoch。

截图请求有 30 秒上限，冷启动 OCR/ONNX 可能需要约 10 秒；建议客户端读超时至少 40 秒。
`observed_at` 始终是截图完成时间，分析耗时不会刷新它。超过 5 秒的截图不能作为动作依据，
首张冷启动图片可能因此需要再取一张。普通动作默认 10 秒、最多 30 秒；
演习启动默认及最多 60 秒。`get_operation` 查询异步结果，工具回执不等于动作完成。

## 按名称技能与撤退

`deploy_operator` 参数继续为 name、location、direction；
`use_skill` / `retreat_operator` 的目标必须且只能选 name 或 location 之一。
name 直接交给持久原生引擎查找，不在 Python 中猜坐标。坐标目标保持原有兼容路径。

```json
{
  "session_id": "s-…",
  "epoch": 3,
  "request_id": "skill-1",
  "expected_snapshot_id": "frame-…",
  "name": "凛御银灰",
  "timeout_ms": 30000
}
```

以上为 `use_skill` 输入示例；撤退使用相同目标结构。name 为 1–128 字符，
location 为两个非负整数，必须在绑定地图范围内。
要求 Agent 控制权、空闲执行器、同 epoch 最新且不超过 5 秒的截图，以及可识别的运行中战斗 HUD。
暂停时返回 `BATTLE_PAUSED`，未知 UI 返回 `BATTLE_STATE_UNKNOWN`，不盲目点击。

异步 Operation 的 `result.native_action_result` 示例：

```json
{
  "action_sequence": 4,
  "batch_index": 0,
  "context_revision": 3,
  "engine_return": true,
  "reason": "OK",
  "game_effect": "unknown"
}
```

`engine_return` 只证明引擎函数返回值。工具接收、任务完成、输入送达、游戏生效分开报告。
false 产生失败终态，不再被 SingleStep 包装为成功；错误包含原生动作结果。
原生原因包括 `ACTION_RETURNED_FALSE`、`ACTION_EXCEPTION`、`CONTEXT_INVALID`、`CANCELLED`。
服务错误包括 `TACTICAL_ACTION_FAILED`、`NATIVE_TASK_FAILED`、`CAPABILITY_UNSUPPORTED`，
以及通用的 `BUSY`、`EPOCH_MISMATCH`、`STALE_OBSERVATION`、`TIMEOUT`、`CANCELLED`。
超时/取消先关闭输入准入并合作停止，无法排空则回收 Worker，不能撤销已发生的动作。

## set_battle_speed

输入为通用动作参数，另加 `speed: integer enum [1,2]`。要求绑定地图、运行时
`absolute_speed` 能力和新鲜截图。暂停或运行中的普通战斗均可设置。
先 OCR 当前倍率（精确 1X/2X 等格式，原始分数至少 0.98），未知时拒绝操作；
目标已达成时 `input_issued=false`，否则只切换一次，并最多三次被动观察确认目标。
验证失败返回 `VERIFICATION_FAILED`，不反复点击。该分数不是校准置信概率。
成功返回 `battle_speed`、`input_issued`、`verification_source="vision"`、`game_effect="verified"`。
暂停/继续也使用目标状态及操作后视觉验证，不能把停止输入当游戏暂停。

```json
{"session_id":"s-…","epoch":3,"request_id":"speed-2","expected_snapshot_id":"frame-…","speed":2}
```

## 只读观察

`get_battle_state`、`get_deployable_operators`、`get_deployed_operators` 输入均为：

```json
{"session_id":"s-…","epoch":3}
```

三者同步读取缓存，不截图、不运行识别、不点击；共享每秒 10 次状态读取上限。
先用 `get_screenshot` 取得新帧（每秒最多 2 次，可用 `max_age_ms` 复用缓存）。
查询返回 snapshot 及字段信封；两个干员查询把相应字段放在 `operators`。

```json
{
  "value": 20,
  "status": "observed",
  "source": "vision",
  "confidence": null,
  "observed_at": "2026-11-17T11:00:00.000Z",
  "evidence_snapshot_id": "frame-…"
}
```

| 字段 | 证据和限制 |
|---|---|
| cost、kills、total_kills、battle_speed | 当前截图的 OCR；不可识别为 unknown |
| paused | 普通 HUD 的播放/暂停图标；受转场、警报染色影响 |
| deployable_operators | 卡片矩形、职业、费用、可用/冷却；名字仅匹配已有头像缓存，可能为空 |
| deployed_operators | 引擎位置记录，source=engine_context、status=estimated；不是视觉盘点 |
| skill_states | 已记录位置上的 Ready 候选、原始分数与概率，status=experimental |
| game_phase | 当前仍为 unsupported/unknown |

confidence 目前保持 null。超过 5 秒的字段标为 stale、value=null。
Ready 自动操作固定关闭；少量样本命中不能证明跨皮肤、地图及特效的可靠性。
观察直接复用上游 BattlefieldMatcher/Classifier，不调用会点击未知干员的 update_deployment，
也不调用可能处理剧情或速度的 check_in_battle。

截图通过标准 Image Content 和 `maatactics://sessions/{session_id}/snapshots/{snapshot_id}.png`
提供，元数据使用同路径 `.json`。状态、作业和 about Resources 保持兼容。
缓存最多 20 帧/64 MiB/120 秒，先达到的限制生效；操作引用的截图应及时保存。
过期 Resource 返回 JSON-RPC -32002，data.code=RESOURCE_EXPIRED，客户端应保留动作终态。
关闭会话元数据保留 5 分钟。`disconnect_device` 只接受 session_id，异步停止并销毁 Worker。

使用 P5 运行时时可通过 [混合接口](p5-interface.md) 暂停作业、修订并恢复。精确过帧仍为 false。
