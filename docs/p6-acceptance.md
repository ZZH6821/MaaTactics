# P6 精简战斗运行时

P6 在 P4、P5 的 MaaCore 增量补丁上增加 `MaaTacticsCore` 构建目标和 `battle-v1` 资源加载器。当前验收平台为 Windows x64。保留上游 Controller、任务调度器、传统 Copilot 及混合战斗控制，通过选择性编译排除基建、肉鸽、日常等任务实现，并通过公开 API 限制紧凑运行时可创建的任务。保留传统 Copilot 引用的 `MedicineCounterTaskPlugin` 和 `DrGrandetTaskPlugin`。该库的 `AsstTacticalCoreProfile()` 返回 `1`，完整配置返回 `0`，战斗和混合控制 ABI 仍为 v1。

## 构建和配置

在 MaaTactics 仓库根目录运行。前提为 Python 3.12、uv、Git、CMake 4.2 或更新版，以及安装了 C++ 工作负载的 Visual Studio 2026。`--reference` 指向包含 `upstream.lock.json` 所记录提交的独立 MAA Git 仓库。

```powershell
uv sync --locked
uv run python tools/build_battle_core.py `
  --reference ../reference/MaaAssistantArknights `
  --work-dir ../.build/maatactics-battle `
  --cache-dir ../.cache/native

uv run python tools/prepare_battle_resources.py `
  --maa-root 'C:\path\to\MAA' `
  --output runtime/battle-v1
```

设备配置的 `runtime_root` 指向 `../.build/maatactics-battle/stage`，`expected_sha256` 取同一构建目录的 `native-build.json` 中 `library.sha256`。复制资源准备工具输出的 `resource_root` 和 `expected_resource_sha256` 至本机配置。`stage` 是精简的本机 Release DLL 目录，运行需要相应的微软 x64 C++ 运行库。MCP 启动方式、ADB 路径和设备地址沿用原配置。

项目仍可用完整 MaaCore 配完整资源。完整库与 `battle-v1` 资源交叉混用会在创建原生句柄前返回 `RESOURCE_UNVERIFIED`。紧凑模式校验每个资源文件的大小及 SHA-256，拒绝缺失、改动或清单外文件。校验失败退出当前 worker。资源使用期按不可变处理，更新时生成新目录、更新两项 SHA-256、关闭旧会话后重新连接。原版本保留在原路径，可通过原配置回滚。安装包更新时，只有留存文件及经筛选的任务和地图产出字节相同才可沿用当前资源配方。要接受实际资源变化，需评审配方差异并重跑原生、画面与设备回归。

## 资源依赖

固定配方 `native/maacore/battle-resources.lock.json` 基于 MAA CN 资源 2026-11-22 11:43。本地生成结果保留 `main_01-07` 和 `main_01-07#f#`，两个地图条目仍须按精确 ID 区分。任务清单包含战斗开始/控制、Copilot 编队支持和间接模板依赖。通过 MaaCore Debug 的 TaskData 任务语法检查及模板查找验证闭包。局部使用共用界面主题节点和退场节点。

保留费用/击杀/名字 OCR、干员卡片模板、技能 Ready 分类器、触控助手及编队所需数据。方向分类和战场干员目标检测模型未进入已验证的被动观察或输入链路。头像缓存由 worker 用户目录维护，新工作目录仍可能无法识别干员名字。自动 Ready 和精确游戏过帧维持关闭。

## 分发

Windows 构建和打包工作流输出原生 ABI 验证记录、Python wheel、源码包及每个包内文件的来源、许可和 SHA-256。两种包均采用 AGPL-3.0-only，包含别名数据所需声明，源码包还包含上游补丁。原生构建在本地核对 MaaUtils/MaaDeps 输入摘要，记录各 DLL 来源。游戏数据、模型权重、minitouch/MaaTouch 以及本机原生 DLL 通过用户自己的 MAA 安装和已锁定的上游构建依赖获取。相关来源分别记录在本机清单和 `THIRD_PARTY_NOTICES.md`。

## Windows 验收结果

| 验收项 | 实测结果 |
|---|---|
| 干净构建与冷启动 | Visual Studio 2026 下从固定基线应用三层补丁，构建 Windows x64 Release，独立 DLL 目录加载成功。Debug 通过任务和模板闭包检查。 |
| 资源体积 | `resource/` 由 9,614 个文件、260.6 MiB 缩至 163 个文件、53.2 MiB，减少 79.6%。 |
| 缺资源与迁移 | 缺模型拒绝冷启动，缺模板拒绝生成，改动和多余文件拒绝加载。完整库与紧凑资源互不兼容。新资源清单要求重新绑定摘要，原资源目录可用于回滚。 |
| 回归 | 65 项普通测试和 5 项无设备原生测试通过。24 张固定截图的逐字段结果与 P4 基线一致。 |
| MuMu 1-7 演习 | 作业在费用条件期间让出控制权。MCP 继续、按名称部署、释放技能、撤退、修订及交还作业均通过。截图核对位置、技能特效及再部署冷却。作业执行索引为 `0, 1, 2, 4, 5`。断开后复核游戏暂停在 2 倍速。 |
| Python 分发 | wheel 从源码包构建，在隔离环境安装，stdio 发现 24 个工具。两个归档均通过逐文件许可和来源审计。 |

P2 的十轮发布稳定性门槛及自动编队的广泛实机覆盖仍单独记录。证据摘要见 [P6 验收记录](validation/p6-checks.json)。
