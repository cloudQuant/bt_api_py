# 迭代10：当前使用文档与退役入口对齐

日期：2026-10-17。仓库：bt_api_py，分支：dev；本轮父提交：f95d2056749e0aaaef69356db7d5344dc9c606b6。

本轮范围为 `docs/guides/usage_guide.md`、`mkdocs.yml` 和本记录。使用页将共享读与受管订单入口对齐到已提交的 Core contract / runtime architecture，移除已退役 forwarding service 与 command-status 路径的使用建议。MkDocs 将 `ITERATION*.md` 历史开发记录排除在当前用户站点之外，保留原始文件及其中指向子仓库的证据链接；当前用户指南仍在导航中。

修改前依次运行文档生成器、文档合同检查和严格构建：前两项 exit 0；严格构建因历史配额交付记录的站点外子包链接失败。修改后 `generate_exchange_support_docs.py --check`、`check_docs_contract.py` 和 `python -m mkdocs build --strict` 全部 exit 0。生成的支持矩阵没有变化。独立只读审查 GO，未重复运行行为测试、Host 或账户调用。

这是一轮文档修复，正式进度保持 61/68。计划要求的四角色启动、账户组合与注册回执的完整可发现示例仍需补齐；不以本轮修复宣称该项已完成。DIM07 / LOAD01 尚待实际负载验收，VENUE 会话与双机条件未改变。
