# P3 Copilot 接口

三个工具均通过标准 MCP stdio 提供。业务实现位于 `application.py`，与 MCP 类型无关。
可机器读取的完整输入 JSON Schema 位于 `src/maatactics/tool-input-schemas.json`，根对象拒绝额外参数；
作业文档允许额外协议字段，保留其 JSON 语义，不重写动作、分组或条件。

## load_copilot

输入：`session_id: string`、`epoch: integer`、`request_id: string`、`document: object`；
可选 `timeout_ms: integer`，1000–60000，默认 60000。
`document` 必须含非空 `stage_name` 和对象数组 `actions`。

前置条件：已连接、epoch 有效、无输入或截图任务运行。每会话至多 16 个不同文档，每文档至多 1 MiB、
10000 动作、64 层嵌套；request_id 去重只保存参数摘要。

立即返回通用 Operation 回执。通过 `get_operation` 等待后取得：

```json
{
  "copilot_id": "copilot-<SHA-256>",
  "sha256": "<canonical JSON digest>",
  "stage_name": "main_01-07",
  "document_action_count": 5,
  "size_bytes": 606,
  "validation": {
    "accepted": true,
    "validation_level": "native_parser_acceptance",
    "runtime_version": "v6.22.0",
    "device_connected": false,
    "tasks_started": false
  },
  "document_uri": "maatactics://sessions/<session_id>/copilots/<copilot_id>.json"
}
```

摘要针对规范序列化后的 UTF-8 JSON，保留字段值和数组次序，不保留原文件的空格和键顺序。
文档 URI 是标准 JSON Resource，不接受任意本地路径或下载 URL。

原生后端在临时子进程中加载外置 MaaCore，验证 DLL 哈希，仅调用 `AsstAppendTask("Copilot", params)`，
随后 Stop/Destroy 并删除临时目录；不连接设备、不调用 AsstStart、不预排活跃 Worker 的任务。
加载失败不会停止现有设备 Worker，也不改变当前 epoch。取消或超时会杀死并回收验证进程。
Mock 明确返回 `mock_structural_only`，不冒充原生解析。

解析接受只证明上游接受该输入，不保证干员、练度、技能、地图画面或动作可执行。
上游忽略的额外字段仍保留在文档中；不承诺所有字段都被运行时消费。
根据固定源版本的动作别名表拒绝未知动作，避免上游默认跳过拼写错误。
支持稳定版三段式 `minimum_required`，在解析后核对原生版本；运行时和源提交仍未建立构建证明。

错误：`COPILOT_INVALID`、`COPILOT_VERSION_UNSUPPORTED`、`CAPABILITY_UNSUPPORTED`、
`RESOURCE_TOO_LARGE`、`SESSION_LIMIT`、`VALIDATOR_FAILED`、`RUNTIME_UNVERIFIED`，
以及通用的 `BUSY`、`EPOCH_MISMATCH`、`REQUEST_CONFLICT`、`TIMEOUT`、`CANCELLED`。

## start_copilot

输入：`session_id`、`epoch`、`request_id`、`expected_snapshot_id`、`copilot_id`；
可选 `formation: boolean = false`、`timeout_ms: integer = 600000`，范围 1000–3600000。

前置条件：已加载文档；已绑定匹配的地图；当前控制者为 Agent；无其他设备操作；
引用最近一次、同 epoch 且不超过 5 秒的截图。当前实机地图验证白名单仍为普通 1-7。
调用者应从截图核对关卡和战前界面；工具会拒绝已识别出的战斗 HUD，
但 `unknown` 不等于已认证的编队界面，错误界面仍可能导致原生重试或失败。

立即返回 Operation，`operation_id` 同时作为 `run_id`。
运行时独占整个输入通道，Agent 动作和新截图请求返回 BUSY；进度查询读取缓存。
原样写入 Worker 私有文件，执行以下固定原生参数：

```json
{
  "filename": "<private immutable revision file>",
  "formation": false,
  "loop_times": 1,
  "use_sanity_potion": false,
  "add_trust": false,
  "support_unit_usage": 0
}
```

`formation=true` 只显式开启上游自动编队。成功路径未做实机认证；缺干员和编队错误有回调注入测试。
普通 Copilot 使用上游 BattleStartAll，可能开始消耗理智的普通战斗；本次实机验收只在演习执行。
不用药、不补信赖、不导航多关卡、不循环。

终态 Operation 包括 `native_task_outcome`、`input_execution`、`game_effect`、前后快照 ID、
文档 ID 和进度。普通战术的 input_execution/game_effect 保持 unknown；
原生 TaskChainCompleted 不证明每步成功，也不证明关卡通关。
完成、失败、取消后旧战术上下文均失效；重新 `set_stage`、取得新 epoch、观察后才能继续 Agent 控制。

取消使用 `cancel_operation(session_id, operation_id)`；紧急停止用 `stop_operations(session_id)`。
两者都不能撤销已经发生的游戏输入。底层 AsstStop 合作取消，无法排空时回收 Worker；
停止输入不会暂停游戏。要保持暂停，应在正常作业结束后单独调用 `pause_battle`，再断开。

错误：`COPILOT_NOT_FOUND`、`STAGE_MISMATCH`、`STAGE_UNSUPPORTED`、
`STALE_OBSERVATION`、`COPILOT_START_PRECONDITION`、`NATIVE_REJECTED`、`NATIVE_TASK_FAILED`、
`DEVICE_DISCONNECTED`，以及通用会话、互斥、超时和取消错误。

## get_copilot_progress

输入：`session_id: string`、`run_id: string`。同步读取缓存，至多每秒 5 次，不操作设备。
返回完整 Operation，另含顶层 `run_id` 和 `progress`：

```json
{
  "source": "native_callbacks",
  "announced_action_count": 3,
  "last_announced_action": {"action": "UseSkill", "target": "主力", "elapsed_time": -1},
  "completed_action_count": null,
  "confirmed_action_index": null,
  "native_chain_outcome": "unknown",
  "game_end_state": "unknown",
  "diagnostics": [],
  "updated_at": "2026-11-16T21:58:41.962Z"
}
```

`CopilotAction` 在条件等待、延迟和操作之前发出，没有可靠索引；宣布计数不能当完成百分比。
子任务错误保留最后 20 条诊断，每条 details 至多 4096 字符；过大的诊断只标 truncated。
运行结果最多保留 50 条原生事件，每条超过 8 KiB 时只保留识别字段和 truncated 标记。
TaskChainError 与普通子任务错误分开处理，因为上游某些准备步骤允许失败。
回调诊断测试涵盖真实源码定义的 `OperatorMissing`，其他编队失败通过故障事件注入验证。
无此运行返回 `OPERATION_NOT_FOUND`，超限返回 `RATE_LIMITED`；关闭会话元数据保留 5 分钟。

## 调用顺序

1. `connect_device` → `get_operation` 等待成功。
2. `load_copilot(document=<examples/copilot/1-7-exercise-smoke.json 的对象>)` → 等待验证成功，保存 copilot_id。
3. `set_stage(stage_name="main_01-07")` → 使用结果中的新 epoch。
4. `get_screenshot` → 核对演习编队界面、关卡和干员。
5. `start_copilot(copilot_id, expected_snapshot_id, formation=false)` → 用 operation_id 查询进度。
6. 运行返回后重新观察；在新 epoch 下调用 `pause_battle`，核对暂停，再 `disconnect_device`。

完整演习证据见 `docs/validation/p3-copilot-scenario.json`；截图保存在本机 ignored artifacts 中。
需要可恢复暂停、修订或接管时，使用支持 P5 的运行时和 `start_copilot(hybrid=true)`。见 [混合接口](p5-interface.md)。
