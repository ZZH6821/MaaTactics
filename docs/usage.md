# 使用说明

当前实机验证环境为 Windows x64、MuMu 和 ADB，关卡为国服普通 1-7 演习。

## 无设备快速开始

在 MaaTactics 源码目录中执行。需要 [uv](https://docs.astral.sh/uv/) 和 Python 3.12。

```powershell
uv sync --locked --python 3.12
uv run --locked pytest -q tests/test_mcp_protocol.py
```

该测试会启动真实 stdio MCP 会话，验证工具发现、Mock 截图、图像 Resource 和连续动作。将以下配置加入支持 `mcpServers` 的客户端，并替换为源码目录的绝对路径。客户端启动进程后，在工具中选择 `device_profile` 为 `mock`。若客户端找不到 `uv`，将 `command` 改为 `uv.exe` 的绝对路径。

```json
{
  "mcpServers": {
    "maatactics": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/MaaTactics", "run", "--locked", "maatactics-mcp", "--mock"]
    }
  }
}
```

## Windows 实机配置

准备已安装国服《明日方舟》的 MuMu 实例、可用的 `adb.exe` 和兼容的 [MAA 安装](https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases)。用 `adb devices -l` 查看连接地址。示例配置使用 `127.0.0.1:16384`，以本机实例实际地址为准。

```powershell
Copy-Item configs/example.toml configs/mumu.local.toml
```

编辑 `configs/mumu.local.toml`，保留 `[devices.mumu]` 并设置 `adb_path` 和 `address`。按所需能力选择一种运行时。

| 运行时 | 用途 | 配置 |
| --- | --- | --- |
| 完整 MAA | 连接、截图、基础 SingleStep、传统 Copilot 和账号干员识别。P0 验收使用 v6.22.0。 | `runtime_root` 指向包含 `MaaCore.dll` 和 `resource/` 的 MAA 安装目录。`expected_sha256` 取下方探针报告。 |
| P6 `battle-v1` | 已验收的持久战斗上下文、按名技能和撤退、战场结构化观察、绝对倍率和 Copilot 混合接管。 | 本地构建战斗 DLL 并生成国服 1-7 精简资源。设置 DLL 和资源清单的两个 SHA-256。 |

### 使用完整 MAA

将命令中的目录替换为本机 MAA 安装位置。探针只加载资源和测试任务入队，无需启动游戏。成功后从 `artifacts/probe/report.json` 复制 `library_sha256` 到 `expected_sha256`。

```powershell
uv run --locked maatactics-probe --runtime-root 'C:\path\to\MAA' --output-dir artifacts/probe
```

### 构建 P6 `battle-v1`

Windows x64 实测工具链为 Visual Studio 2026 C++ 和 CMake 4.4.5。构建脚本会从固定 MaaCore 提交导出源码，应用三层补丁并校验 MaaUtils 和 MaaDeps。下列命令在源码目录执行，构建和缓存都位于 Git 忽略的 `build/`。

```powershell
$maaCommit = '67478591379d441e25a59156047c3f6163520fb7'
git init build/maa-reference
git -C build/maa-reference fetch --depth=1 https://github.com/MaaAssistantArknights/MaaAssistantArknights.git $maaCommit

uv run --locked python tools/build_battle_core.py `
  --reference build/maa-reference --work-dir build/battle-core --cache-dir build/native-downloads
uv run --locked python tools/prepare_battle_resources.py `
  --maa-root 'C:\path\to\MAA' --output runtime/battle-v1
```

在本机配置中设置以下字段。构建和资源准备的输出目录须是新目录。

- `runtime_root` 指向 `build/battle-core/stage` 的绝对路径。
- `expected_sha256` 取 `build/battle-core/native-build.json` 的 `library.sha256`。
- `resource_root` 和 `expected_resource_sha256` 取资源准备工具的输出。精简配方锁定已验收的 MAA 国服资源，若输入摘要发生变化，先使用完整 MAA 方案验证设备连接。资源兼容与迁移见 [P6 构建和验收](p6-acceptance.md)。

### 验证连接并接入 MCP 客户端

以下命令通过 MCP 连接、保存截图并断开，只用于检查设备接入。截图和清理回执写入 `artifacts/device/`。

```powershell
uv run --locked python tools/capture_device.py --config configs/mumu.local.toml --profile mumu
```

连接成功后，将快速开始的客户端参数末尾从 `--mock` 改为 `"--config", "configs/mumu.local.toml"`。实机调用使用 `device_profile` 为 `mumu`。建议 MCP 客户端读超时至少 40 秒，冷启动时要加载识别模型。

## MCP 会话操作

1. 调用 `connect_device`，传入 `device_profile` 和唯一的 `request_id`。用回执中的 `session_id`、`operation_id` 调用 `get_operation`，等到 `lifecycle` 为 `completed`。
2. 调用 `set_stage`，用 `get_session_status` 获得当前 epoch，实机普通 1-7 使用精确的 `stage_name` 值 `main_01-07`。作业或动作继续使用完成回执里的最新 epoch。`set_stage` 只绑定坐标，进入演习编队需要在游戏界面操作。
3. 调用 `get_screenshot` 或 `get_battle_state` 查看战场。截图可读取 Image Content 或回执中的 `image_uri`，识别字段可能为 `unknown`。
4. 已进入匹配的演习编队时，可调用 `start_battle`，默认在识别到普通战斗 HUD 后暂停。继续使用 `resume_battle` 后再发战术操作。动作使用当前 `session_id`、epoch、新 `request_id` 和 5 秒以内的 `expected_snapshot_id`。截图过期时重新观察。开始战斗或上下文重置后重新读取 epoch。
5. 观察动作前后截图核验游戏效果。`accepted` 表示请求入队，`completed` 表示任务结束，`game_effect=unknown` 时由客户端判断是否生效。
6. 结束时先按需调用 `pause_battle` 并确认生效，再执行 `disconnect_device`。`stop_operations` 和断开只停止自动输入，游戏会保持当前状态。

头像、技能就绪和暂停识别受画面条件影响。暂停状态未知时，服务会拒绝切换。自动释放 Ready 技能和精确过帧的验证进度见 [开发路线图](roadmap.md)。

传统作业通过 `load_copilot` 校验原格式 JSON，`start_copilot` 开始执行，`get_copilot_progress` 查询进度。开始前核对游戏界面和关卡，普通出击可能消耗理智。混合模式使用 `start_copilot(hybrid=true)`，依次通过 `pause_copilot`、`revise_copilot` 和 `resume_copilot` 接管、修订和交还作业。详细契约见 [Copilot 接口](copilot-interface.md)、[战场观察和控制接口](p4-interface.md) 和 [混合控制接口](p5-interface.md)。

## 账号干员识别

在完整 MAA 设备配置中设置 `roster_account = 'main'`。这是本地账号别名，以小写字母或数字开头，之后可用小写字母、数字、下划线和连字符，最多 64 个字符。切换游戏账号时使用相应别名的设备配置。

1. 通过 `connect_device` 连接。在游戏首页，或已取消筛选且滑到最左侧的干员列表，用 `get_screenshot` 获取最新截图并确认画面。
2. 用同一会话的 `session_id`、`epoch`、新的 `request_id` 和 5 秒内的 `expected_snapshot_id` 调用 `scan_operator_roster`。用 `get_operation` 等待完成。扫描会翻页并独占设备输入，默认超时 5 分钟。可用 `cancel_operation` 取消。
3. 用 `get_operator_roster(account_key="main")` 离线读取。UTF-8 JSON 保存在 `data_root/rosters/account-main.json`，包含账号别名、扫描时间、来源、MaaCore 版本以及 `operators`。每个条目包含 `id`、`name`、`rarity`（星级）、`elite`（精英阶段）、`level`（等级）和 `potential`（潜能）。P6 配置可通过同一 `data_root` 读取快照。

每次成功扫描更新该账号最近一份快照。识别或保存失败、取消、超时时保留上一份。扫描结束后使用新的 `epoch` 重新观察，战术操作需要重新绑定关卡。字段取自 MaaCore 的截图识别，可能有漏项或误识别，等级 OCR 失败时上游可能回填 1。

## 本地测试

```powershell
uv run --locked ruff check src tests tools
uv run --locked pytest -m 'not native and not device'
```

原生回归须显式配置本机运行时。实机验收脚本要求事先确认普通 1-7 的演习编队，操作步骤见 [P5 实机验收](p5-acceptance.md)。
