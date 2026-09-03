# 安装与本地诊断

## Python policy

Python 3.11、3.12、3.13 是 release-blocking CI 版本；3.14 只运行 canary，不代表无条件发布支持。项目不声明 Python 3.9 或 3.10 支持。

## 安装 wheel

```bash
python -m pip install --upgrade pip
python -m pip install bt_api_py
python -m bt_api_py.doctor --bundle core-reference --format json
```

doctor 从已安装包中的 bundle metadata 读取信息。它的成功只证明 wheel 资源和诊断路径可用；可选插件缺失时会报告 `disabled`/原因，不会把该结果写成连接或实盘认证。

## 源码开发

```bash
git clone --recurse-submodules https://github.com/cloudQuant/bt_api_py
cd bt_api_py
python -m pip install -e ".[dev]"
```

没有使用 `--recurse-submodules` 时，先初始化所需插件：

```bash
git submodule update --init --recursive
```

插件验证必须在隔离环境完成：

```bash
python scripts/ci/submodule_validation.py \
  --profile core-reference \
  --artifacts-dir /private/tmp/bt-api-py-core-reference-artifacts
```

结果会包含 JSON、JUnit 和每个安装/导入/测试阶段的日志。一个不存在或无法安装的插件是明确的 `unavailable`/失败结果，而非已支持状态。
