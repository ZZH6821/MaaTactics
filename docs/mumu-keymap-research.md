# MuMu 键位参考与暂停控制

用户在 2026-11-17 要求将 `pause_battle` / `resume_battle` 提前为 MCP 必需功能。
这覆盖原计划中首版暂不提供游戏暂停/继续的安排；停止自动输入仍与游戏暂停分开。

## 参考文件

工作区外置参考：`reference/Mumu_keymap/com.hypergryph.arknights-【16比9 60帧 V3.6】by 云迹CloudTrace.json`。
文件署名为云迹 CloudTrace，当前目录未附独立 LICENSE。没有将整份配置复制或重新授权到正式项目。

已检查的方案：

| 功能 | 宿主触发键 | 实现要点 |
|---|---|---|
| 开局暂停 | C | 按住期间每 5 ms 循环内部键 158 的按下/释放 |
| 暂停/继续 | Space | 一个 Click 映射，修改按键 0 的映射后使用 |
| 时停 | F | 内部键 158 的按下与释放 |
| 前进 166 ms | Y | 内部键 11，10 ms 按下，再等待 155 ms 并点击暂停区域 |
| 前进 33 ms | T | 类似流程，第二段等待为 22 ms |
| 前进 12 ms | R | 类似流程，第二段等待为 12 ms |

文件中的宿主 virtual_key、scan_code、宏内部 keyboard_press 编号以及 Android ADB keyevent
是不同编号空间，不能把内部 158 或 11 直接传给 `adb input keyevent`。
该配置通过定时事件和相对点击近似控制进度，未提供游戏帧号反馈，因此不能单凭名称宣称实际精确前进 N 帧。

## 当前实现

- 暂停状态由被动截图观察：普通 16:9 战斗 HUD 标志和右上角播放/暂停图标。
- `pause_battle` 和 `resume_battle` 输入契约与单动作共享 session_id、epoch、request_id、expected_snapshot_id、timeout_ms。
- 应用执行器先观察当前状态，只在需要变更时通过同一个 MaaCore Worker 发出一次 AsyncClick。
- 操作后再次观察并确认目标状态；未知界面拒绝输入，失败时不盲目重复切换。
- 幂等调用不反转已经达到的目标状态；截图证据和 confidence=null 保留在结果中。
- 此规则仅覆盖普通战斗 HUD；未知 UI 返回 unknown，不当作游戏运行或已暂停。

## 过帧后续验证

MuMu CLI 的 control 帮助列出快捷键控制入口，具体按键注入和触发宏行为仍需查验。
未来必须具备有界按键按下/释放、finally 释放、窗口绑定、帧率/倍速标注和实测偏差记录。
在取得可验证的游戏帧反馈前，能力名称应描述为有时限的运行窗口，并分别返回请求时长、实际宿主耗时及帧数 unknown。
当前 `precise_frame_step=false`，不自动导入键位、不执行无限开局循环。
