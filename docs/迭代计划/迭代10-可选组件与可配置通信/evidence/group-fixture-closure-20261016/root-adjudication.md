## 第125轮：组服务测试的已提交依赖闭合

将两个已提交组服务测试依赖的SQLite合成provider叶子抽为独立stdlib测试helper；provider account改为实例参数，消除对未提交exposure测试以及其risk-only测试全局变量的依赖。未改生产代码；Root核对两个原测试的81/88条Assert AST均未改变，三个候选与独立artifact逐字节一致。独立静态review GO。

在唯一的新非Git artifact中，从SDK ed5ce2e及已提交的Base b4aabd1e、Execution ae1dffd4、Risk 5f6ba0b2、Gateway 59b164ab、Transport 29ab21e7、Monitor 536a4f04导出源码，只覆盖本轮三个测试路径。一次 --collect-only 成功收集两个目标节点，exit0；JUnit tests=0，没有运行测试体或启动Host。582个artifact源码pins前后一致，stderr为空；保留一条禁用插件自动加载所致的pytest配置warning。Root核对原spec/result/stdout/JUnit、当前候选、全部artifact pins；没有重复第121轮已接受的七项运行验收。

验收等级 ARTIFACT_COLLECTION_ONLY：接受测试依赖闭合与可收集性，不宣称新运行行为、发行安装、真实账户或ARB完整矩阵通过。当前SDK/dev父ed5ce2e，提交三个明确test/helper及本记录，保留其他开发改动，正式22/68不变。此提交为CFG04从固定提交制作安装验收支持文件解除依赖阻塞。

证据见[evidence/group-fixture-closure-20261016/root-adjudication.md](evidence/group-fixture-closure-20261016/root-adjudication.md)。
