# 明日方舟作业知识库与信息源

建议从国服普通 1-7 演习开始。仓库维护来源索引、简短的战术笔记和可复核的作业修订结果。运行时坐标沿用已验收的 MAA 资源，账号练度和实机原图从本地读取。AI 可据此解释作业为什么失败、提出具体改动，并用下一轮演习检验改动。调查日期为 2026 年 11 月 24 日。

## 信息源

| 来源 | 用途 | 采用条件 |
| --- | --- | --- |
| [鹰角国服公告][official] | 查更新公告、活动开放与官方确认的修复。 | 用于判断旧笔记是否需要复核，具体引用对应公告。 |
| [MAA 战斗流程协议][maa-protocol]和[本仓库接口](copilot-interface.md) | 写 Copilot JSON，查动作、坐标、条件和练度字段。 | 执行前以本仓库所用 MaaCore 版本的解析结果和[资源锁](../native/maacore/battle-resources.lock.json)为准。 |
| [ArknightsGameData][game-data] | 首选机读研究源。国服关卡配置可查地块、路线、刷怪动作，数据表可查干员、技能和模组。 | 社区解析镜像。更新提交注明客户端版本和数据版本，适合锁定快照。按实际需要读取文件。 |
| [ArknightsAssets/ArknightsGamedata][assets-data] | 自动更新的社区数据镜像，包含 `cn`、`bili`、`en`、`jp`、`kr`、`tw` 目录。可交叉核对关卡、敌人、干员、技能和模组数据。 | 按目标服务器选目录，记录 Git 提交和该服数据版本。国服官方渠道使用 `cn/gamedata`。 |
| [ArknightsGameResource][game-resource] | 补查机读字段、关卡索引或其他镜像缺失的文件。 | 社区镜像。先核对数据版本。可用不同镜像检查解析差异，同源字段的一致性只说明该项解析一致。 |
| [PRTS Wiki 作战机制][prts-mechanics]、[游戏数据基础][prts-data]及对应关卡、干员、敌人页 | 查中文术语、难度修正、敌人行为及伤害等组件如何在实战中交互。 | 记录固定链接、章节、适用难度和例外。影响作业成败的推断用演习确认。 |
| [PRTS.Map][prts-map] | 人工查看地形、敌人路线和布阵。 | 地图覆盖随站点更新。部署动作仍按 MAA 坐标和当前画面确认。 |
| [PRTS Plus][copilots] | 查玩家作业，从中提取部署顺序、分组和触发条件的候选改法。 | 记录作业 ID、作者、关卡和练度要求。用于本机前，核对编队与地图并先跑演习。[MAA 作业说明][maa-manual] |
| [MaaTactics 实机验收](p5-acceptance.md)和[本地干员名册](usage.md#账号干员识别) | 分别确认改动的实际效果和账号可用的干员、精英阶段、等级、潜能。 | OCR 字段可能缺失或误识别。候选作业要求的技能、模组等练度应按实际编队确认。 |

如果以后将刷图收益纳入目标，可用企鹅物流查询玩家上报的掉落统计。记录服务器、统计时段和样本数。它的 API 数据采用 CC BY-NC 4.0。[企鹅物流 API 文档][penguin]

PRTS Wiki 对站点原创内容默认采用 CC BY-NC-SA 4.0，并单列游戏原始素材的权利。ArknightsGameData 和 ArknightsAssets/ArknightsGamedata 的项目首页均未列出许可证，ArknightsGameResource 的 README 另行声明静态资源的权利归属。仓库可保存来源链接、核验日期、版本和项目自写的结论。镜像数据、Wiki 正文、游戏素材和第三方作业按需引用，批量重新分发前逐项确认授权。[PRTS 版权声明][prts-license]、[ArknightsGameData 项目首页][game-data]、[ArknightsAssets 项目首页][assets-data]、[ArknightsGameResource 项目声明][game-resource]

## 国服普通 1-7 样例

- **关卡身份。** `main_01-07` 是普通，`main_01-07#f#` 是突袭。两者显示代码都是 1-7，且共用 `Obt/Main/level_main_01-07`。作业索引用服务器和精确 `stageId`，另记是否演习，避免把突袭的费用规则用于普通演习。[关卡表快照][pinned-stages]、[MAA CN 资源锁](../native/maacore/battle-resources.lock.json)
- **机读源抽查。** Kengxxiao 的 `7e56005` 和 yuanyan3060 的 `e03bf6e` 均可读取关卡表、1-7 地图、敌人数据库及干员、技能、模组表。两者的 `data_version.txt` 同为 `Change:128540`、`VersionControl:79.4.0`，1-7 地图维度及路线数也相同。后一个提交更新了派生索引，提交日期更新时仍需核对客户端数据版本。[Kengxxiao 提交][keng-commit]、[yuanyan3060 提交][yuan-commit]、[Kengxxiao 数据版本][keng-version]、[yuanyan3060 数据版本][yuan-version]
- **ArknightsAssets 国服快照。** `4cf0cdf` 的 `cn/gamedata` 数据版本同为 `Change:128540`、`VersionControl:79.4.0`。1-7 的两个 `stageId`、共用的 `levelId`、地图维度和路线数与另两份镜像一致。该提交更新的是 `en` 目录，判断国服数据是否更新时应查看 `cn` 的版本文件。[ArknightsAssets 提交][assets-commit]、[国服数据版本][assets-version]
- **游戏状态。** PRTS Wiki 普通难度页修订 445782 记录 41 个待处理目标，P5 演习的 checkpoint 也报告 `total_kills=41`。可把它用作本场景的复核项。实时触发仍使用当前运行中已确认的费用、击杀、技能和画面状态。[PRTS 1-7][prts-17]、[P5 实机记录](validation/p5-device-scenario.json)
- **作业证据。** P5 样例在费用等待处交权，实机观察确认了部署、技能、撤退以及修订后以 2 倍速继续。P3 样例的 `native_task_outcome` 为 `completed`，而视觉复核记录的 `battle_cleared` 为 `false`。动作验证可引用这些记录，通关改进需记录游戏结算证据。[P5 作业样例](../examples/copilot/1-7-hybrid-smoke.json)、[P5 验收](p5-acceptance.md)、[P3 实机记录](validation/p3-copilot-scenario.json)

## 作业试验记录

首批记录可以直接沿用 Markdown。每条记录保留这些内容，后续 Agent 能按关卡和失败现象检索并复跑。

| 内容 | 要记录的值 |
| --- | --- |
| 适用范围 | 服务器、精确 `stageId`、演习或实战、游戏数据版本、MaaCore 版本与资源摘要。 |
| 失败现象 | 原作业路径或 ID、修订号、动作索引、费用或击杀等触发条件、可用干员和关键练度、对应观察证据。 |
| 改动假设 | 引用的原始资料和版本、改哪一步、预期看到的效果。 |
| 验证结果 | 基准和候选各跑几次、用的编队和开局条件、动作效果、游戏结算和失败原因。附仓库验收记录或本地证据摘要。 |

先通过 `load_copilot` 检查作业，并在同一关卡、编队和运行时条件下比较基准与候选。混合运行中可用 `revise_copilot` 修订尚未执行的动作。若只有解析或动作证据，就按该范围记录结果。遇到游戏更新、资源摘要变化或机制冲突时，保留原条目的适用版本，复核影响作业的字段并补记新结论。[Copilot 接口](copilot-interface.md)、[混合控制接口](p5-interface.md)

[official]: https://ak.hypergryph.com/news
[maa-protocol]: https://docs.maa.plus/zh-cn/protocol/copilot-schema.html
[maa-manual]: https://docs.maa.plus/zh-cn/manual/introduction/copilot.html
[game-data]: https://github.com/Kengxxiao/ArknightsGameData
[assets-data]: https://github.com/ArknightsAssets/ArknightsGamedata
[game-resource]: https://github.com/yuanyan3060/ArknightsGameResource#声明
[prts-mechanics]: https://prts.wiki/w/作战机制
[prts-data]: https://prts.wiki/w/游戏数据基础
[prts-map]: https://mapcn.ark-nights.com/
[prts-17]: https://prts.wiki/w/1-7
[prts-license]: https://prts.wiki/w/MediaWiki:Copyright
[copilots]: https://prts.plus/
[penguin]: https://developer.penguin-stats.io/public-api
[keng-commit]: https://github.com/Kengxxiao/ArknightsGameData/commit/7e560050b03c34661db1d322d008733072352b2a
[assets-commit]: https://github.com/ArknightsAssets/ArknightsGamedata/commit/4cf0cdfdfa28e2b30c0c7c87b271073fcb7ec062
[yuan-commit]: https://github.com/yuanyan3060/ArknightsGameResource/commit/e03bf6e75142ef9a45df5c9742c8a037c465d6e7
[pinned-stages]: https://github.com/Kengxxiao/ArknightsGameData/blob/7e560050b03c34661db1d322d008733072352b2a/zh_CN/gamedata/excel/stage_table.json
[keng-version]: https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/7e560050b03c34661db1d322d008733072352b2a/zh_CN/gamedata/excel/data_version.txt
[assets-version]: https://raw.githubusercontent.com/ArknightsAssets/ArknightsGamedata/4cf0cdfdfa28e2b30c0c7c87b271073fcb7ec062/cn/gamedata/excel/data_version.txt
[yuan-version]: https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/e03bf6e75142ef9a45df5c9742c8a037c465d6e7/gamedata/excel/data_version.txt
