# Iteration10：持久资金账本的公开投影

范围：组合 API 公开 financial 的 absent/unavailable/available 状态；隐藏内部账户/执行 scope key，纳秒时间转字符串，保留币种资金、费用、成本缺失与独立 cut。页面单独显示持久账本，不加到 provider 账户总额；没有新增 FX 或估值。

仓库/分支：ai-for-investor/dev；验收父提交 e9c7ca1d6149ddae176f090f98dba4cee52dfe58，实际提交SHA见本文件 Git 历史。仅本轮五个源码/测试文件与本记录，原有 Windows/README 修改保留。

独立静态审查 GO。Root backend 2 passed（6.39秒、1912来源pins不变，1条既有Quandl弃用警告）；实际 FastAPI HTTP + ServiceReadWorker + 合成 session 验证接线。前端2项受影响组件测试通过，35项未选；123.45 USDT资金未计入950 USDT总额。初次typecheck只发现fixture错误码字面量变宽，增加 as const 后typecheck exit0；该类型注释没有运行时变化，已通过测试未重复。

原始证据：D:/temp/iteration10-afi-financial-http-root-20261016 和 D:/temp/iteration10-afi-financial-frontend-root-20261016；SDK迭代10 evidence/afi-financial-projection-20261016 归档对应输出。证据等级为合成HTTP/组件，不是完整AFI02/03或账户验收。
