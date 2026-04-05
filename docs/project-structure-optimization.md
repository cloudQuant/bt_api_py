# 项目结构优化计划

> 创建时间: 2026-04-05
> 状态: 待实施

## 问题分析

### 1. 配置文件重复
- **问题**: 根目录 `configs/` 和包内 `bt_api_py/configs/` 两个配置目录并存
- **影响**: 维护困难，容易产生配置不一致
- **建议**: 合并到 `bt_api_py/configs/`，删除根目录 `configs/`

### 2. 根目录文件混乱
- **问题**: 大量临时报告和中间文件
- **文件列表**:
  - `agent_8_improvement_report.md`
  - `code_quality_analysis.txt`
  - `FINAL_SUMMARY.md`
  - `IMPLEMENTATION_SUMMARY.md`
  - `IMPROVEMENT_REPORT.md`
  - `IMPROVEMENT_REPORT_FINAL.md`
  - `WEBSOCKET_OPTIMIZATION_SUMMARY.md`
  - `agent_tasks/` 目录
- **建议**: 
  - 保留核心文档: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY_COMPLIANCE.md`
  - 其他报告文件移动到 `docs/archive/` 或删除

### 3. scripts 目录臃肿
- **问题**: 115 个文件，包含大量临时脚本
- **冗余目录**:
  - `agent_tasks/`
  - `agent_tasks_even/`
  - `agent_tasks_new/`
- **建议**:
  - 保留核心脚本: 测试运行、文档生成、部署脚本
  - 删除临时批量处理脚本
  - 合并重复的 agent_tasks 目录

### 4. 文档目录杂乱
- **问题**: 正式文档与临时报告混合
- **建议**:
  - 保留正式文档: `getting-started/`, `guides/`, `reference/`, `explanation/`
  - 临时报告移动到 `docs/archive/` 或删除

### 5. 错误处理分散
- **问题**: `error.py` 和 `errors/` 目录并存
- **建议**: 明确职责分工
  - `error.py`: 错误基类和核心错误类型
  - `errors/`: 各交易所错误翻译器

### 6. 测试目录结构
- **问题**: 根目录下大量分散的测试文件
- **建议**: 按功能模块重新组织

## 优化方案

### 阶段一: 清理临时文件 (低风险)

```bash
# 1. 删除根目录临时报告
rm -f agent_8_improvement_report.md
rm -f code_quality_analysis.txt
rm -f FINAL_SUMMARY.md
rm -f IMPLEMENTATION_SUMMARY.md
rm -f IMPROVEMENT_REPORT.md
rm -f IMPROVEMENT_REPORT_FINAL.md
rm -f WEBSOCKET_OPTIMIZATION_SUMMARY.md
rm -f None  # 空文件

# 2. 删除临时 agent_tasks 目录
rm -rf agent_tasks/
rm -rf scripts/agent_tasks/
rm -rf scripts/agent_tasks_even/
rm -rf scripts/agent_tasks_new/
```

### 阶段二: 合并配置目录 (中风险)

```bash
# 1. 检查重复配置
diff -r configs/ bt_api_py/configs/

# 2. 合并唯一配置到 bt_api_py/configs/
# 3. 更新代码中的配置路径引用
# 4. 删除根目录 configs/
```

### 阶段三: 整理文档目录 (低风险)

```bash
# 1. 创建归档目录
mkdir -p docs/archive/reports

# 2. 移动临时报告
mv docs/improvement-report*.md docs/archive/reports/
mv docs/code-quality*.md docs/archive/reports/
mv docs/*-report.md docs/archive/reports/
mv docs/*-plan.md docs/archive/reports/
```

### 阶段四: 精简 scripts 目录 (低风险)

保留核心脚本:
- `run_*.sh` - 测试运行脚本
- `generate_*.py` - 文档生成脚本
- `check_code_quality.py` - 代码质量检查
- `analyze_*.py` - 分析脚本

删除临时脚本:
- `batch_improve*.py` - 批量改进脚本 (已完成任务)
- `improve_*.py` - 临时改进脚本
- `temp_*.py` - 临时文件
- `fix_*.py` - 临时修复脚本

## 目标结构

```
bt_api_py/
├── bt_api_py/                  # 核心包
│   ├── configs/                # 所有配置文件 (合并后)
│   ├── containers/             # 数据容器
│   ├── core/                   # 核心模块
│   ├── ctp/                    # CTP 扩展
│   ├── errors/                 # 错误翻译器
│   ├── exchange_registers/     # 交易所注册
│   ├── feeds/                  # 交易所适配层
│   ├── functions/              # 工具函数
│   ├── gateway/                # 网关模块
│   ├── monitoring/             # 监控模块
│   ├── risk_management/        # 风险管理
│   ├── security_compliance/    # 安全合规
│   ├── websocket/              # WebSocket 模块
│   ├── __init__.py
│   ├── auth_config.py
│   ├── bt_api.py               # 统一 API 入口
│   ├── error.py                # 错误基类
│   ├── event_bus.py
│   ├── exceptions.py
│   ├── registry.py
│   └── ...
├── docs/                       # 文档
│   ├── getting-started/        # 入门指南
│   ├── guides/                 # 使用指南
│   ├── reference/              # API 参考
│   ├── explanation/            # 概念解释
│   ├── exchanges/              # 交易所文档
│   ├── archive/                # 归档文档
│   └── index.md
├── tests/                      # 测试
│   ├── containers/
│   ├── feeds/
│   ├── core/
│   └── ...
├── scripts/                    # 核心脚本
│   ├── run_tests.sh
│   ├── generate_docs.py
│   └── ...
├── examples/                   # 示例代码
├── .github/                    # GitHub 配置
├── README.md                   # 项目说明
├── CHANGELOG.md                # 变更日志
├── CONTRIBUTING.md             # 贡献指南
├── pyproject.toml              # 项目配置
└── Makefile                    # 构建脚本
```

## 实施优先级

1. **高优先级**: 阶段一 (清理临时文件) - 风险最低，效果明显
2. **中优先级**: 阶段三 (整理文档) - 改善文档结构
3. **中优先级**: 阶段四 (精简 scripts) - 减少维护负担
4. **低优先级**: 阶段二 (合并配置) - 需要代码修改，风险较高

## 注意事项

1. 所有删除操作前先备份
2. 合并配置目录需要更新代码引用
3. 保持测试通过
4. 更新 .gitignore 防止临时文件再次提交
