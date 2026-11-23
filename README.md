# MaaTactics

An agent-ready tactical combat engine for Arknights, powered by MAA and exposed through MCP.

MaaTactics 通过本地 MCP 服务将《明日方舟》的战场观察和战斗操作开放给外部 Agent。Agent 可以获取截图和结构化战场状态，部署干员、释放技能、撤退、暂停战斗和调整倍率。项目基于 MAA 的 MaaCore，也支持运行现有 Copilot 作业，并在安全同步点由 Agent 接管和交还控制权。战术推理由客户端完成。

## 文档

- [使用说明](docs/usage.md)
- [版本更新](docs/changelog.md)
- [开发路线图](docs/roadmap.md)
- [实施状态与验收](docs/implementation-status.md)

源码采用 [AGPL-3.0-only](LICENSE)。上游代码、依赖和资源的来源见 [第三方声明](THIRD_PARTY_NOTICES.md)。
