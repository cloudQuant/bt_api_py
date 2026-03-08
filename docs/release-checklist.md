# 发布前检查清单

**最后更新：** 2026-03-08
**版本：** 0.15
**适用场景：** 每次发布新版本前必须完成此清单

本文档提供完整的新版本发布前检查清单，确保发布质量、稳定性和安全性。

---

## 📋 检查清单总览

| 类别 | 项目数 | 必填 | 选填 |
|------|--------|------|------|
| 代码质量 | 5 | 5 | 0 |
| 测试验证 | 5 | 5 | 0 |
| 文档更新 | 4 | 4 | 0 |
| 依赖管理 | 3 | 2 | 1 |
| 安全审查 | 3 | 3 | 0 |
| 打包发布 | 4 | 3 | 1 |
| 发布后验证 | 2 | 2 | 0 |
| **总计** | **26** | **24** | **2** |

---

## ✅ 代码质量检查

### 1.1 Linting 检查

- [ ] 运行 `ruff check bt_api_py/ tests/` 无错误
- [ ] 运行 `ruff format bt_api_py/ tests/` 格式化代码
- [ ] 修复所有SIM、B、F类警告
- [ ] E501行长度警告应通过formatter自动处理
- [ ] CTP相关文件的N802/N803/N806警告是允许的

**命令：**
```bash
make lint
make format
# 或
ruff check bt_api_py/ tests/
ruff format bt_api_py/ tests/
```

**验证标准：**
- 输出应为 `All checks passed!`
- 没有错误级别（E、F类）问题

---

### 1.2 类型检查

- [ ] 运行 `mypy bt_api_py/` 类型检查通过
- [ ] CTP相关模块使用 `ignore_errors = true`
- [ ] 测试模块使用 `disallow_untyped_defs = false`
- [ ] 修复所有明确错误（非Optional导入问题）

**命令：**
```bash
make type-check
# 或
mypy bt_api_py/
```

**验证标准：**
- 输出应为 `Success: no issues found`
- 允许的忽略项已正确配置

---

### 1.3 代码风格检查

- [ ] 符合PEP 8标准（由ruff enforce）
- [ ] 遵循项目代码风格指南（AGENTS.md）
- [ ] 使用双引号（由ruff enforce）
- [ ] 行长度限制100字符（由ruff enforce）
- [ ] 使用4空格缩进（由ruff enforce）

**验证标准：**
- `ruff format` 后代码格式一致
- 符合AGENTS.md中的代码风格要求

---

### 1.4 导入组织检查

- [ ] 所有导入按标准库、第三方库、本地库排序（由ruff isort处理）
- [ ] 无未使用的导入（由ruff F401/F403处理）
- [ ] 无循环导入
- [ ] 无相对导入问题

**验证标准：**
- 运行 `ruff check` 无F401/F403警告
- 手动检查复杂导入场景

---

### 1.5 注释和文档字符串

- [ ] 公开API有完整的docstring
- [ ] 复杂逻辑有注释说明
- [ ] 遵循Google风格docstring（AGENTS.md）
- [ ] 注释使用中文说明业务逻辑（允许）
- [ ] 技术文档使用英文（AGENTS.md）

**验证标准：**
- 主要类和函数有docstring
- 关键算法有注释

---

## 🧪 测试验证检查

### 2.1 目标测试运行

- [ ] 运行修改影响的相关测试
- [ ] 所有测试通过（无failures）
- [ ] 无跳过非预期测试
- [ ] 测试覆盖率未降低
- [ ] 测试执行时间在合理范围内

**命令：**
```bash
# 运行特定模块测试
pytest tests/feeds/test_binance.py -v

# 运行影响区域测试
pytest tests/containers/test_binance_order.py tests/feeds/test_binance.py -v

# 带覆盖率运行
pytest tests/ --cov=bt_api_py.feeds.live_binance
```

**验证标准：**
- 无测试失败
- 新增测试覆盖新功能

---

### 2.2 非网络回归测试

- [ ] 运行 `pytest tests -m 'not network'` 所有通过
- [ ] 无网络相关测试被意外跳过
- [ ] 单元测试全部通过
- [ ] 集成测试（非网络）全部通过

**命令：**
```bash
make test
# 或
pytest tests -m 'not network' -q
```

**验证标准：**
- 输出应为 `X passed, Y skipped`
- 无failures或errors

---

### 2.3 特定交易所测试

- [ ] 运行所有生产就绪交易所测试
- [ ] 运行修改的交易所测试
- [ ] WebSocket相关测试通过
- [ ] REST API相关测试通过
- [ ] 数据容器测试通过

**命令：**
```bash
# 测试特定交易所
pytest tests -m binance -v
pytest tests -m okx -v
pytest tests -m ctp -v

# 测试WebSocket
pytest tests/websocket/ -v

# 测试数据容器
pytest tests/containers/ -v
```

**验证标准：**
- 特定标记测试全部通过
- 无回归问题

---

### 2.4 性能和稳定性测试

- [ ] 运行性能测试（如有）
- [ ] 测试执行时间无异常增长
- [ ] 内存使用无异常
- [ ] 无内存泄漏（长期运行测试）

**命令：**
```bash
# 性能测试
pytest tests/ -m performance -v

# 内存监控
pytest tests/ --memray-top
```

**验证标准：**
- 性能指标无显著退化
- 内存使用稳定

---

### 2.5 测试覆盖率检查

- [ ] 运行 `pytest --cov` 生成覆盖率报告
- [ ] 总覆盖率不低于60%（pyproject.toml配置）
- [ ] 新增代码有测试覆盖
- [ ] 关键路径有测试覆盖
- [ ] 查看 `htmlcov/index.html` 报告

**命令：**
```bash
make test-cov
# 或
pytest tests/ --cov=bt_api_py --cov-report=html

# 查看报告
open htmlcov/index.html
```

**验证标准：**
- 覆盖率 >= 60%
- 新功能有测试

---

## 📚 文档更新检查

### 3.1 文档内容更新

- [ ] 更新README.md版本号和变更日志
- [ ] 更新CHANGELOG.md（如有）
- [ ] 更新项目概览统计（文件数、测试数）
- [ ] 更新API文档（如有新增或变更）

**验证标准：**
- 文档版本与代码版本一致
- 变更日志包含本次更新

---

### 3.2 交易所能力矩阵更新

- [ ] 更新 `docs/exchanges/EXCHANGE_CAPABILITY_MATRIX.md`
- [ ] 更新 `docs/exchanges/EXCHANGE_STATUS.md`
- [ ] 新增交易所添加到矩阵
- [ ] 状态变更更新到矩阵

**验证标准：**
- 矩阵信息与实现状态一致
- 统计数据准确

---

### 3.3 项目上下文更新

- [ ] 更新 `_bmad-output/project-context.md`
- [ ] 更新日期和版本信息
- [ ] 记录本次重大变更
- [ ] 更新技术栈版本（如有变更）

**验证标准：**
- context信息反映当前状态

---

### 3.4 文档构建验证

- [ ] 运行 `mkdocs build` 成功
- [ ] 无构建错误或警告
- [ ] 检查生成的文档链接有效性
- [ ] 验证示例代码可运行

**命令：**
```bash
cd docs
mkdocs build

# 验证
open site/index.html
```

**验证标准：**
- 构建成功
- 无死链

---

## 📦 依赖管理检查

### 4.1 依赖版本检查

- [ ] 更新 `pyproject.toml` 依赖版本（如需要）
- [ ] 检查依赖安全漏洞
- [ ] 测试依赖版本兼容性
- [ ] 无废弃依赖

**命令：**
```bash
# 检查安全漏洞
pip-audit

# 检查过时依赖
pip list --outdated
```

**验证标准：**
- 无已知高危漏洞
- 依赖版本兼容

---

### 4.2 可选依赖管理（必填）

- [ ] 确认所有可选依赖在 `[project.optional-dependencies]` 中定义
- [ ] 标注哪些依赖是可选的（文档说明）
- [ ] 测试核心功能不依赖可选包
- [ ] 验证可选依赖安装后功能正常

**可选依赖组：**
- `dev` - 开发工具（pytest, ruff, mypy等）
- `visualization` - 可视化（matplotlib, pyecharts等）
- `monitoring` - 监控（psutil等）
- `email` - 邮件（aiosmtplib等）
- `time` - 时间同步（ntplib等）
- `ib` - Interactive Brokers（ib-insync等）
- `ib_web` - IB Web（pyjwt等）
- `cookies` - Cookie（browser-cookie3等）
- `all` - 所有可选依赖

**验证标准：**
- 核心功能不依赖可选包
- 安装可选依赖后功能正常

---

### 4.3 依赖兼容性测试（选填）

- [ ] 测试Python 3.11兼容性
- [ ] 测试Python 3.12兼容性（如声明支持）
- [ ] 测试Python 3.13兼容性（如声明支持）
- [ ] 测试不同操作系统（Linux/macOS/Windows）

**命令：**
```bash
# 在不同Python版本测试
python3.11 -m pytest tests/ -m 'not network' -q
python3.12 -m pytest tests/ -m 'not network' -q
python3.13 -m pytest tests/ -m 'not network' -q
```

**验证标准：**
- 所有支持的Python版本测试通过

---

## 🔒 安全审查检查

### 5.1 敏感信息检查

- [ ] 无硬编码的API密钥、密码、Token
- [ ] 无调试信息泄露敏感数据
- [ ] 日志不输出敏感信息
- [ ] `.env` 文件在 `.gitignore` 中
- [ ] 无临时凭证文件被提交

**命令：**
```bash
# 检查敏感信息
grep -r "api_key\|secret\|password\|token" --include="*.py" bt_api_py/ | grep -v "test\|mock\|example"

# 检查.gitignore
cat .gitignore | grep -E "\.env|credentials|secret"
```

**验证标准：**
- 无敏感信息泄露
- .gitignore包含必要条目

---

### 5.2 代码安全审计

- [ ] 使用 `bandit` 进行安全扫描（如可用）
- [ ] 检查SQL注入风险
- [ ] 检查命令注入风险
- [ ] 检查不安全的反序列化
- [ ] 检查不安全的随机数生成

**命令：**
```bash
# 安全扫描（如果安装了bandit）
bandit -r bt_api_py/
```

**验证标准：**
- 无高危安全问题
- 中低风险问题有评估

---

### 5.3 依赖安全检查

- [ ] 运行 `pip-audit` 检查已知漏洞
- [ ] 检查依赖供应链安全
- [ ] 验证依赖来源可信
- [ ] 无已知恶意依赖

**命令：**
```bash
# 依赖安全审计
pip-audit

# 或使用safety
safety check
```

**验证标准：**
- 无已知CVE漏洞
- 依赖来源可信

---

## 📦 打包发布检查

### 6.1 版本号管理

- [ ] 更新 `pyproject.toml` 中的版本号
- [ ] 遵循语义化版本（Semantic Versioning）
  - MAJOR.MINOR.PATCH
  - MAJOR：不兼容的API变更
  - MINOR：向后兼容的功能新增
  - PATCH：向后兼容的bug修复
- [ ] 更新 `__init__.py` 中的 `__version__`（如有）
- [ ] 标记Git tag

**命令：**
```bash
# 更新版本（示例）
# 编辑 pyproject.toml: version = "0.15.1"

# 创建Git tag
git tag -a v0.15.1 -m "Release v0.15.1"
git push origin v0.15.1
```

**验证标准：**
- 版本号符合语义化版本规范
- Git tag已创建

---

### 6.2 构建测试

- [ ] 运行 `python -m build` 成功
- [ ] 检查生成的wheel文件
- [ ] 检查生成的源码包
- [ ] 验证包内容完整

**命令：**
```bash
# 安装构建工具
pip install build twine

# 构建
python -m build

# 检查输出
ls -la dist/
```

**验证标准：**
- 构建成功
- dist/目录包含wheel和tar.gz

---

### 6.3 打包完整性检查（必填）

- [ ] 检查wheel文件完整性
- [ ] 检查源码包完整性
- [ ] 验证包包含必要文件
- [ ] 检查包大小合理

**命令：**
```bash
# 解包检查
tar -tzf dist/bt_api_py-0.15.1.tar.gz

# 或检查wheel
unzip -l dist/bt_api_py-0.15.1-py3-none-any.whl
```

**验证标准：**
- 包结构正确
- 包含必要文件

---

### 6.4 发布到PyPI测试（选填）

- [ ] 先发布到TestPyPI验证
- [ ] 安装TestPyPI版本测试
- [ ] 验证安装后功能正常
- [ ] 确认无误后发布到正式PyPI

**命令：**
```bash
# 发布到TestPyPI
twine upload --repository testpypi dist/*

# 测试安装
pip install --index-url https://test.pypi.org/simple/ bt_api_py

# 测试功能
python -c "import bt_api_py; print(bt_api_py.__version__)"

# 发布到正式PyPI
twine upload dist/*
```

**验证标准：**
- TestPyPI版本可安装
- 功能正常

---

## ✅ 发布后验证检查

### 7.1 安装验证

- [ ] 从PyPI安装最新版本
- [ ] 验证版本号正确
- [ ] 运行基本功能测试
- [ ] 验证文档可访问

**命令：**
```bash
# 安装
pip install bt_api_py==0.15.1

# 验证
python -c "import bt_api_py; print(bt_api_py.__version__)"
python -c "from bt_api_py.registry import ExchangeRegistry; print('OK')"
```

**验证标准：**
- 安装成功
- 导入正常

---

### 7.2 文档验证

- [ ] 确认在线文档已更新
- [ ] 验证文档链接有效
- [ ] 检查示例代码可运行
- [ ] 确认发布说明清晰

**命令：**
```bash
# 访问在线文档
open https://cloudquant.github.io/bt_api_py/
```

**验证标准：**
- 文档可访问
- 内容正确

---

## 🚨 常见问题排查

### 测试失败

**问题：** 测试在某些环境失败

**解决：**
- 检查Python版本是否兼容
- 检查是否需要特定操作系统
- 检查是否需要特定依赖
- 跳过网络相关测试：`pytest tests -m 'not network'`

### 构建失败

**问题：** `python -m build` 失败

**解决：**
- 清理构建缓存：`rm -rf build/ dist/`
- 检查pyproject.toml语法
- 检查依赖是否安装
- 检查文件权限

### 发布失败

**问题：** `twine upload` 失败

**解决：**
- 检查PyPI凭据
- 检查版本号是否已存在
- 检查包名是否被占用
- 检查网络连接

---

## 📝 发布记录模板

### 版本：X.Y.Z

**发布日期：** YYYY-MM-DD
**发布人：** [姓名]

#### 变更内容

- [新增] 功能1
- [改进] 功能2
- [修复] Bug修复1
- [文档] 文档更新

#### 测试结果

- 单元测试：X/X 通过
- 集成测试：X/X 通过
- 覆盖率：XX%
- 性能测试：通过/不通过

#### 已知问题

- 问题描述
- 影响范围
- 临时解决方案

#### 下一步计划

- 计划中的改进
- 待修复的问题

---

## 📞 获取支持

- **文档**: https://cloudquant.github.io/bt_api_py/
- **GitHub**: https://github.com/cloudQuant/bt_api_py
- **Issues**: https://github.com/cloudQuant/bt_api_py/issues
- **Email**: yunjinqi@gmail.com

---

**注意：** 本检查清单应严格执行。任何未完成项都应记录并解决后才能发布。

**最后验证：** 2026-03-08 by BMAD Team
