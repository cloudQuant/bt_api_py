# 子模块验证 Profiles

子模块验证的目标是留下可复现的包级证据，不是把已初始化的仓库数量误写成“已支持交易所数量”。执行入口为：

```bash
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python scripts/ci/submodule_validation.py \
  --profile core-reference \
  --artifacts-dir /private/tmp/bt-api-py-core-reference-artifacts
```

每个插件都会使用独立 virtual environment；`bt_api_base` 先构建 wheel，再安装进每个插件环境。验证器为 `resolve`、`venv`、`base_install`、`install`、`import`、`collection`、`test` 分别保存 stdout/stderr。因此缺失子模块、构建失败、依赖冲突、导入失败、测试收集失败和测试超时是不同结论。

可用 profile：

| Profile | 选择方式 | CI 用途 |
| --- | --- | --- |
| `core-reference` | 从 `exchange-bundles.toml` 的同名 bundle 加 `bt_api_base` | 子模块变更 PR 的候选 gate |
| `native-build` | CTP/MT5 等原生构建路径 | 诊断 |
| `external-service` | 需要外部服务或凭证的路径 | 诊断 |
| `experimental` | 当前已初始化/可发现的子模块 | 诊断 |
| `all` | 合并前述 profile 并去重 | 定时全量诊断 |

产物目录固定包含：

- `submodule-validation.json`：机器可读的完整结果；
- `submodule-validation.junit.xml`：CI 测试系统可消费的索引；
- `submodule-validation.md`：供人查看的链接索引；
- `logs/<package>/<phase>.stdout.log` 与 `.stderr.log`：失败根因。

`--diagnostic` 只让命令在产生失败证据时继续返回零，绝不把 JSON 中的失败改成成功。只有 `core-reference` 在同一 SHA、干净 runner 和完整 artifacts 下连续成功后，维护者才可以另行申请把更多 profile 变成 required check。
