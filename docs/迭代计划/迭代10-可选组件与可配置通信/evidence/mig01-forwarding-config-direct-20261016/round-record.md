# 第216轮：旧 forwarding_config 入口提前拒绝

Root 接受本轮两路径修复：任何非 None 的旧 forwarding_config 在 logger/EventBus/plugin/feed 创建前具体拒绝，保持原 ZMQ 迁移诊断；普通 None 配置仍使用 DirectBackend，正常查询及关闭，无新旧 forwarding 导入。成功邻例的插件初始化由无副作用记录叶端代替，不冒充完整 provider 插件链。

Root 静态审查后一次运行两个新节点：2 passed in 1.01s，exit0、无 warning、stderr空；46来源pins及 SDK/base HEAD 前后相同。原有已接受入口节点未重复。未启动真实账户或Host。本轮不等于MIG01完整旧目录/导出/插件/发行闭包退役，正式仍39/68。

交付：两源码/测试、三份证据和进度共6路径，现有SDK dev，parent dfddff7fcc88a3a464757617d295dfb723cd5e88。实际commit见Git历史与 .codex-tmp/iteration10-r216-postcommit.json。无关在途改动不纳入。
