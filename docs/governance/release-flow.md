# 发布流程（Release Flow）

> 状态：生效中（迭代03 M5，2026-08-23）。本流程由 `.github/workflows/publish.yml`
> 机械强制。生产 PyPI 只能由受保护 `master` 可达的 tag 与 GitHub Release 触发；
> 手动 dispatch 永远无法选择 PyPI。

## 前置条件（决策门 D4，当前 blocked）

发布前必须由管理员完成并留存 API 证据：

1. 创建 `pypi` / `testpypi` GitHub Environments（当前仅有 `github-pages`）。
2. 在 PyPI/TestPyPI 项目设置中绑定 trusted publisher（仓库、workflow 文件名、
   environment 名称）。
3. 启用 `v*` tag Ruleset（`.github/governance/rulesets/release-tags.json`），
   bypass 名单仅含 D4 确认的 release actor。

**D4 未解除前，TestPyPI 演练与正式发布都不得执行。**

## 发布顺序（不可调换）

```text
1. dev → master promotion PR 合并（或 hotfix PR 直接进入 master）
        │
2. 在目标 master SHA 上 dispatch publish.yml（expected_sha = 该 SHA）
        │   workflow 校验：checkout SHA == expected_sha 且该 SHA 从 master 可达
        ▼
3. TestPyPI 发布成功后，fresh venv 安装 bt_api_py==<version> 冒烟通过
        │
4. 对同一 SHA 打 vX.Y.Z tag（tag 必须与包版本一致——build job 强制校验）
        │
5. 基于 tag 创建 GitHub Release（release: published 触发 pypi environment）
        │
6. PyPI 验证：pip install bt_api_py==X.Y.Z；核对 dist-meta/SHA256SUMS.txt
```

任何一步失败即停止：

| 失败点 | 动作 |
|---|---|
| expected_sha 不匹配 / 非 master 可达 | workflow 自动失败；修正输入重试 |
| TestPyPI 发布或冒烟失败 | **不创建 Release、不发布 PyPI**；在 `dev` 修复后重新 promotion；版本号已被占用时提升版本号 |
| Release 已发布但发现严重问题 | 停止后续 Release；PyPI yank + 新版本修复；留事件 issue |

## 职责

- **Release manager（D4）**：执行 dispatch、创建 tag/Release、核对 SHA256SUMS。
- **管理员**：维护 Environments、trusted publisher、tag Ruleset；每次变更前后
  运行 M0 只读命令并存脱敏摘要。
- **任何人**：不得把手动 dispatch 描述为"已发布生产"；不得绕过 promotion 直接收 master。

## 审计链

每个发布必须能回答四个一致的问题：Git SHA 是什么？包版本是什么？
artifact SHA256 是什么？TestPyPI 冒烟记录在哪里？（证据存
`docs/governance/evidence/` 脱敏摘要，不提交安装包与原始日志。）
