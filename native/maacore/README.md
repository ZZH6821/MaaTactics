# MaaCore P4 扩展

此目录只维护补丁和构建记录，不复制整个 MAA，不包含游戏资源或模型权重。
基线、MaaUtils 提交、MaaDeps 版本及工具版本见 `manifest.json` / `toolchain.lock.json`。
补丁以 AGPL-3.0-only 发布；外部 MaaUtils 为 LGPL v3。

新建 Windows x64 环境优先使用 [P6 `battle-v1` 构建与验收](../../docs/p6-acceptance.md)。以下保留完整资源后端和 P4/P5 的历史验证方法。

## 已有实现与证据

- 每个 Assistant 独立持有持久 TacticalBattleTask；单步任务只保存自己的动作及绑定修订号。
- 队列中的旧动作遇到关卡修订变化时拒绝执行；不再使用进程静态关卡字符串。
- 成功路径保留位置、技能设置、镜头映射及计时状态；失败路径清空上下文并改变修订号。
- `TacticalActionCompleted` 包含单调动作序号、批内索引、engine_return、原因和上下文修订号。
- engine_return 仅表示引擎函数返回值；输入实际送达及游戏效果仍须另行验证。
- 只读接口对传入的 1280×720 PNG 运行 BattlefieldMatcher / BattlefieldClassifier，复用原生模型前处理。
- 未知干员保持未知；观察只匹配已有头像缓存，禁止通过点击详情页获取名字。
- 内存契约、原生并发/取消、固定截图、实机命名动作及绝对速度验收通过；见 `docs/validation/p4-*.json`。
- Ready 只有少量单皮肤样本，保持实验输出并关闭自动操作；P2 发布稳定性门槛仍独立保留。

## 扩展 ABI v1

```c
uint32_t AsstBattleAbiVersion(void);
AsstBool AsstBattleReset(AsstHandle handle, const char* stage_name);
AsstSize AsstBattleGetContext(AsstHandle handle, void* buffer, AsstSize buffer_size);
AsstSize AsstBattleObserve(AsstHandle handle, const void* png, AsstSize png_size,
                         void* buffer, AsstSize buffer_size);
```

遵循 AsstCaller 的 Windows 调用约定。Reset 要求无运行任务及排队任务；空关卡清除上下文。
查询返回 UTF-8 JSON 的字节数，不含结尾 NUL；空缓冲或容量不足时只返回所需长度。
无效句柄或异常返回 0；上下文正被动作占用时返回 `ok=false,error=BUSY`，不阻塞观察。
Observe 最多接受 8 MiB PNG；不调用设备、update_deployment 或 check_in_battle。
返回的部署位置来自 engine_context，技能 Ready 为 experimental，分数不是校准概率。
当前所有自动 Ready 驱动功能均关闭。

## 本机构建

在工作区根目录执行，源码导出到 MaaTactics 仓库之外；参考仓库不会被改动。
需固定版本 CMake、clang-format 23.1.1，以及 VS 2026 C++ 工具链。

```powershell
MaaTactics/.venv/Scripts/python.exe MaaTactics/tools/prepare_maacore.py `
  --reference reference/MaaAssistantArknights --output .build/maacore-source `
  --cache-dir .cache/native

# 已准备过的目录直接复用，不重新导出覆盖。
git -c core.autocrlf=false -C .build/maacore-source apply ../../MaaTactics/native/maacore/patches/0001-p4-tactical-context.patch
.tools/native/Scripts/python.exe .build/maacore-source/tools/maadeps-download.py x64-windows --cache-asset

$debugZlib = (Resolve-Path '.build/maacore-source/src/MaaUtils/MaaDeps/vcpkg/installed/maa-x64-windows/debug/lib/zsd.lib').Path
.tools/native/Scripts/cmake.exe -S .build/maacore-source -B .build/maacore-build `
  -G 'Visual Studio 18 2026' -A x64 -DBUILD_WPF_GUI=OFF -DBUILD_DEBUG_DEMO=OFF `
  -DBUILD_RESOURCE_UPDATER=OFF -DWITH_EMULATOR_EXTRAS=OFF -DMAADEPS_TRIPLET=maa-x64-windows `
  '-DMAA_HASH_VERSION=v6.22.0-maatactics-p4.2' -DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded `
  "-DZLIB_LIBRARY_DEBUG=$debugZlib"
.tools/native/Scripts/cmake.exe --build .build/maacore-build --config Debug --target MaaCore --parallel 4

MaaTactics/.venv/Scripts/python.exe -m maatactics.backends.maacore.p4_probe `
  --runtime-root .build/maacore-build/bin/Debug --resource-root 'C:\path\to\MAA' `
  --output-dir MaaTactics/artifacts/p4-native
```

预构建依赖下载后必须核对 release asset SHA-256，实测摘要已存入 toolchain.lock.json。
源码/补丁更新后重新构建并记录 DLL 摘要，旧配置的 expected_sha256 不自动放行。
P4 本机配置使用独立 `configs/p4.local.toml`；库目录和资源目录分开，不覆盖已安装的 MaaCore.dll。
以上 ZLIB 路径应按构建目录调整。上游查找曾为 Debug 目标选中 release `zs.lib`（/MD），
显式选择 `debug/lib/zsd.lib`（/MDd）后，LNK4098 已消除；未使用 NODEFAULTLIB 掩盖冲突。
补丁在全新基线文件上正向应用后，13 个文件与实际构建源码逐字节一致；反向检查及
clang-format 23.1.1 检查也通过。模型分类器源文件未修改，前处理直接复用上游。

仅验证 Windows x64 Debug 开发构建；尚未认证 Release、跨平台或可独立分发的原生包。
如需运行无设备输入的 P4 回归，设置 `MAATACTICS_P4_NATIVE_RUNTIME` 和
`MAATACTICS_P4_RESOURCE_ROOT` 后执行 `pytest -m native`。普通 CI 不加载 DLL。

## P5 混合控制构建

应用 `0001-p4-tactical-context.patch` 后，在同一源码树应用 `patches/0002-p5-hybrid-control.patch`。重新运行上面的 CMake 配置，将 `MAA_HASH_VERSION` 改为 `v6.22.0-maatactics-p5.0`，再构建 `MaaCore`。P5 记录位于 `p5-manifest.json`。按实际 DLL 的 SHA-256 更新本机配置。

P5 在 P4 ABI v1 上增加两个可选 C API 符号。状态查询返回 UTF-8 JSON。下发请求使用 64 KiB 固定输出缓冲，调用成功后以 `command_id` 匹配 `last_command.id`。

```c
AsstSize AsstHybridGetState(AsstHandle handle, void* buffer, AsstSize buffer_size);
AsstSize AsstHybridControl(AsstHandle handle, const char* command, void* buffer, AsstSize buffer_size);
```

设置 `MAATACTICS_P5_NATIVE_RUNTIME` 和 `MAATACTICS_P4_RESOURCE_ROOT` 后运行 `pytest -m native`。P5 Debug 演练仅接受未连接设备的句柄和内存动作。实际设备测试需单独启用 `tools/verify_hybrid_device.py`，以已确认的 1-7 演习编队开始。
