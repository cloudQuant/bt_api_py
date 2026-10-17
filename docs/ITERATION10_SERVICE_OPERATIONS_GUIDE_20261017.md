# 迭代10：服务配置与生命周期操作指南

2026-10-17，bt_api_py 现有 dev；父提交 b194a8a5d691f8298f49ac73a70ff2baf060895d。实际提交 SHA 见本文件 Git 历史。

新增当前 ServiceHost/ServiceClientRuntime 操作指南、九个配置示例和站点导航。覆盖 Gateway 行情、账户 Gateway/Execution/Risk 组合、Monitor 入口/查询、Risk 分析、零配置、多策略/多账户及端点对应形状；解释可信部署依赖、注册摘要、同线程构造/启动/维护/关闭、UNKNOWN 与不确定关闭时保留 owner、离线迁移。

独立静态审查已核对配置与已提交 API；两处建议已修正：客户端从构造开始必须在同一线程，CLI 使用仓库中真实路径。Root 发布时只调整文档链接和仓库路径，九份 YAML 与作者通过离线解析的草稿逐字节相同；作者的九新加五旧共14份离线配置解析已通过，无服务/账户调用，未为交付重复解析。变更前生成器、docs contract 和 strict build 均通过；变更后生成器 --check、docs contract 和 mkdocs build --strict 均 exit 0；源配置与行为测试未重复运行。

示例为配置形状与 API 装配说明，不包含密钥或可直接启动真实账户的 authority/provider。双机形状使用 TEST-NET 文档地址，不能据此宣称双机验收。未新增正式 PASS，61/68；LOAD01 pilot 与 DIM07 条款裁定单独报告。
