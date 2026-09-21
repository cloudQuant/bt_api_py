# Git 仓库辅助脚本

本目录中的下列脚本用于维护 `bt_api_py` 与 `bt_api/*` 子仓库。Windows 请调用 `.bat`，macOS/Linux/Git Bash 请调用同名 `.sh`。

## 统一切换分支

`switch_all_branches` 将根仓库和所有已初始化的递归子仓库统一切换到 `dev` 或 `master`，并从 `origin` 仅做 fast-forward 同步。

```bat
scripts\switch_all_branches.bat dev
scripts\switch_all_branches.bat master
```

```sh
./scripts/switch_all_branches.sh dev
./scripts/switch_all_branches.sh master
```

脚本在切换前会确认：目标仅为 `dev`/`master`、每个仓库工作树干净、每个 `origin/<branch>` 存在。任意预检失败时不会开始切换；脚本不会执行 `reset`、强推或创建 merge commit。

切换子仓库分支可能使根仓库显示 Gitlink 变更。这表示根仓库记录的子仓库提交与所选分支头不同，是发版前需要审阅并提交的正常状态。

## 更新发布 Gitlink

`update_gitlinks` 根据每个一级子仓库当前的 `HEAD`，仅暂存根仓库中的 Gitlink 变更。它适合在子仓库提交、推送并完成验证之后，在根仓库准备发版提交时调用。

```bat
scripts\update_gitlinks.bat --check
scripts\update_gitlinks.bat
```

```sh
./scripts/update_gitlinks.sh --check
./scripts/update_gitlinks.sh
```

`--check` 只显示将发生的 Gitlink 差异，不暂存任何内容。无参数模式会执行 `git add -- <每个一级子仓库>`，只将 Gitlink 暂存，不提交、不推送，也不会暂存根仓库的其他文件。脚本会在任意递归子仓库未初始化、有未提交修改或有 Gitlink 冲突时停止。

## 推荐发版顺序

1. 在所有需要发布的子仓库完成代码变更、测试、提交和推送。
2. 使用 `switch_all_branches` 将根仓库和子仓库切到要发布的分支。
3. 运行 `update_gitlinks --check` 审阅子模块指针变化。
4. 运行 `update_gitlinks` 暂存 Gitlink，然后执行 `git diff --cached --submodule=log` 复核。
5. 连同版本号、变更日志与文档，在根仓库提交；运行 CI 后再创建 tag 和 Release。

不要用 `git submodule update` 代替第 2 步：该命令会按根仓库已记录的提交检出子仓库，通常会使子仓库进入 detached HEAD，而不是统一到 `dev` 或 `master`。
