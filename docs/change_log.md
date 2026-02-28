### 修改日志

- [x] 2026-02-28 完善项目文档体系
  - 重写 README.md：新增项目特性、支持交易所表格、架构概览、快速开始指南、项目结构说明
  - 新增 docs/architecture.md：核心架构设计文档，包含 Registry 模式、Container 模式、Feed/Stream 系统、数据队列、事件总线、异常体系等
  - 新增 docs/usage_guide.md：完整 API 使用教程，覆盖 REST/异步/WebSocket、余额管理、历史数据下载、多交易所、CTP/IB 使用、错误处理等
  - 新增 docs/developer_guide.md：开发者指南，包含添加新交易所 5 步流程、新数据容器、测试编写、代码规范
  - 新增 docs/index.md：文档导航索引，统一入口引用所有文档
  - 完善 docs/change_log.md：补充历史变更记录

- [x] 2026-02-27 生成 AI 项目上下文文件
  - 新增 _bmad-output/project-context.md：AI Agent 项目规则和模式参考

- [x] 2026-02-26 更新 IBKR Web API 文档
  - 更新了 trading.md、account_management.md 和 index.md 的时间戳
  - 新增 api_reference_quick.md：中文快速参考指南，包含常用端点、参数和示例
  - 新增 implementation_guide.md：中文实现指南，包含 Python 代码示例、认证配置、核心功能实现和最佳实践
  - 为后续创建对接 IB 的接口做好文档准备

- [x] 2025-02-03 增加了统一接口 BtApi 可以直接连接多个交易所
