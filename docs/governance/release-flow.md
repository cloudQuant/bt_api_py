# 发布流程（Release Flow）

> 状态：本地候选身份合同已实施，外部发布仍为 **NO-GO**（迭代07，2026-09-20）。
> 本流程由 `.github/workflows/publish.yml` 与 `scripts/ci/release_candidate.py` 机械强制。
> 手动 dispatch 只构建和归档候选，永远不写 TestPyPI/PyPI；生产链仅能由受保护
> `master` 可达的 `vX.Y.Z` GitHub Release 触发，并要求仓库变量
> `ENABLE_PYPI_RELEASE == true`。

## 前置条件（决策门 D4，当前 blocked）

发布前必须由管理员完成并留存 API 证据：

1. 创建并在线复核 `pypi` / `testpypi` GitHub Environments（“仅有 `github-pages`”是历史观察，本轮未联网刷新，不能视为当前事实）。
2. 在 PyPI/TestPyPI 项目设置中绑定 trusted publisher（仓库、workflow 文件名、
   environment 名称）。
3. 启用 `v*` tag Ruleset（`.github/governance/rulesets/release-tags.json`），
   bypass 名单仅含 D4 确认的 release actor。

4. 从官方来源核验并把发布工作流使用的第三方 action 固定到不可变完整 commit SHA；
   当前 `actions/*@vN` 与 `pypa/gh-action-pypi-publish@release/v1` 仍是可移动引用。
5. 以带哈希的 release-build lock 固定 `build`、`twine` 及其完整传递依赖；不得在候选
   摘要生成前执行浮动的在线构建工具链。
6. 将候选校验留在无 OIDC 权限的 job；最终持有 `id-token: write` 的发布 job 不 checkout、
   不执行候选仓库脚本，只消费已验证的不可变 artifact/digest。
7. 仅在上述证据齐备后设置 `ENABLE_PYPI_RELEASE=true`；默认缺失/`false` 时，所有
   registry 写入 job 均跳过。

**D4 未解除前，不得设置发布开关，也不得执行 TestPyPI/PyPI 外部写入。**

## 发布顺序（不可调换）

```text
1. dev → master promotion PR 合并（或 hotfix PR 直接进入 master）
        │
2. 可选：在目标 master SHA 上手动 dispatch（expected_sha = 完整 40 位小写 SHA）
        │   只生成候选 artifact；不写 TestPyPI/PyPI
        ▼
3. 完成 D4，设置 ENABLE_PYPI_RELEASE=true；对目标 SHA 打 vX.Y.Z tag
        │   tag 必须与 pyproject 版本一致
        ▼
4. 发布 GitHub Release，触发同一次受控运行：
        │   build → publish-testpypi → smoke-install-testpypi → publish-pypi
        │
5. 每个下游阶段下载并复验原 build artifact：
        │   source SHA / version / wheel+sdist filename/SHA256 / manifest SHA256
        │
6. TestPyPI 传播完成后，只下载精确版本 wheel，核验文件名与 SHA256；
        │   fresh venv 从已校验本地 wheel 安装，依赖仅从 PyPI 解析
        ▼
7. smoke 全部通过后，PyPI job 再次复验同一候选并发布
```

build 阶段强制 `dist/` 恰好包含一个 wheel 和一个 sdist，生成
`dist-meta/release-candidate.json` 与 `dist-meta/SHA256SUMS.txt`。候选清单绑定 source SHA、
version、wheel/sdist 文件名、大小、SHA256 和 wheel-contract receipt。下游除了校验清单本身，
还必须消费 build job 输出的 wheel/sdist filename/SHA256 与 manifest SHA256 独立锚点；不得重新构建、使用
`skip-existing`、忽略缺失 artifact 或用合并索引模糊选择根包。

任何一步失败即停止：

| 失败点 | 动作 |
|---|---|
| expected_sha 不匹配 / 非 master 可达 | workflow 自动失败；修正输入后只重新构建候选 |
| 发布开关未启用 | TestPyPI/smoke/PyPI 全部跳过；不得将 build artifact 表述为已发布 |
| TestPyPI 发布或冒烟失败 | PyPI job 因 `needs` 链停止；版本号已被 TestPyPI 占用时提升版本后重新 promotion |
| 候选文件名、摘要、source 或 version 不一致 | 立即失败；禁止替换 artifact 或绕过复验 |
| GitHub Release 已发布但后续失败 | 生产 PyPI 保留未写入；release manager 撤回或标记失败 Release，并留存事件记录 |
| PyPI 已发布后发现严重问题 | PyPI yank + 新版本修复；不得覆盖原版本 |

`release: published` 意味着 GitHub Release 元数据先于 TestPyPI smoke 存在。现有工作流可保证
smoke 失败时不写生产 PyPI，但不能把“Release 已创建”当作“包已通过发布验收”；这也是
AC-17 在真实 hosted run 完成前维持 **PARTIAL / NO-GO** 的原因之一。

## 职责

- **Release manager（D4）**：可执行 build-only dispatch；在 D4 完成后设置一次性发布开关、
  创建 tag/Release，并核对候选清单、wheel SHA256 与 job 依赖链。
- **管理员**：维护 Environments、trusted publisher、tag Ruleset；每次变更前后
  运行 M0 只读命令并存脱敏摘要。
- **任何人**：不得把手动 dispatch 描述为"已发布生产"；不得绕过 promotion 直接收 master。

## 审计链

每个发布必须能回答五个一致的问题：Git SHA 是什么？包版本是什么？wheel 文件名是什么？
artifact SHA256 是什么？TestPyPI 精确候选冒烟记录在哪里？证据存
`docs/governance/evidence/` 的脱敏摘要，不提交安装包、凭证或原始日志。

当前仅有本地脚本/工作流结构合同；GitHub hosted、TestPyPI/PyPI、OIDC、Environment、Ruleset
和 trusted publisher 均 **NOT_RUN**。第三方 action 的不可变 SHA、构建依赖哈希锁与 OIDC
最小执行面也尚未完成，禁止据此
宣称发布能力通过。
