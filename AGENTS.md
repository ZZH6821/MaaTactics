# MaaTactics 分支与提交流程

## 开始任务

- 用 `git status --short --branch`、`git branch -vv` 和 `git worktree list` 确认工作区、上游和已有分支。保留用户的未提交改动。已有工作区和任务分支可用时，继续在原处工作。
- 本地 `main` 跟踪 `origin/main`。开始新任务时先 `git fetch origin`，再从 `origin/main` 创建主题分支。用 `feat/<主题>`、`fix/<主题>` 或 `docs/<主题>` 命名。更新干净的本地 `main` 时使用 `git pull --ff-only`。遇到分叉时先查看双方独有的提交，再决定如何同步。
- 如果任务提交已落在本地 `main`，先用 `git switch -c <主题分支> main` 保全它们。确认工作区干净、`origin/main` 是原 `main` 的祖先、原 `main` 的提交均可从主题分支到达，且其他工作区未检出 `main` 后，再用 `git branch -f main origin/main` 对齐本地默认分支。

## 验证与提交

- 把实现、相应测试和使用说明按目的组织成提交。沿用 `feat:`、`fix:`、`docs:` 等提交前缀。与当前任务无关的改动留给原任务处理。
- 修改代码时运行与 GitHub Actions 一致的检查。涉及原生运行时或设备行为时，按[使用说明](docs/usage.md)补充相关验证，并记录实际运行的检查和结果。

  ```powershell
  uv run --locked ruff check src tests tools
  uv run --locked ruff format --check src tests tools
  uv run --locked pytest -m 'not native and not device'
  ```

- 按路径暂存文件，再用 `git diff --cached --check`、`git diff --cached --stat` 和 `git diff --cached` 检查提交内容。账号名单、实机截图、日志、构建产物和 `*.local.toml` 保存在本地忽略目录或忽略路径中。提交后用 `git status --short --branch` 核对剩余改动。

## 推送、评审与收尾

- 任务包含远程发布时，用 `git push -u origin <主题分支>` 发布，再用 `git ls-remote` 核对远程分支和本地提交 SHA。先检查该分支是否已有 PR，有则沿用。需要评审和 CI 时向 `main` 开 PR，写明变更、验证结果和运行时限制。
- 通过追加提交更新已发布的主题分支。直接修改远程默认分支、改写已发布历史和合并 PR 由用户明确指定。合并前检查 PR 的目标分支、变更范围和 CI 结果。
- 合并后用 `git fetch origin` 更新远程引用，再快进本地 `main`。清理已完成的分支前确认 PR 已合并、分支没有额外提交，且没有工作区仍在使用。交接时报告当前分支、提交 SHA、PR、CI 状态和剩余事项。
