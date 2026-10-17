# 第288轮：ARB06 正式验收

Root 按正式迭代计划第772行裁定 **ARB06 PASS**，正式通过由60/68增至**61/68**。
本轮是正常 Cerebro/Store/Broker 与风险服务、合成 provider 的本机验收，不是交易所账户或双机验收。

R10 一次完整节点：**1 passed / 17.60秒 / exit 0**，2条既有warning（Quandl弃用、Windows ZMQ Proactor selector兼容），stderr为空；901来源pin及8个仓库HEAD前后相同。实际输出在 `D:/temp/arb06-full-close-committed-r10-20261017/host-output-r10/`。
九件精确原始输出已压缩保存于 [root-r10-output.zip](root-r10-output.zip)，SHA-256 `16b7033ab04e53114385b3e5779305aa03f573c1a03d4e0bf50aa114d89e990c`；解压读取逐字节与原输出核对相同。未归档901份源码。

## 判定证据

- 两个真实策略共享账户；源持仓1.5加peer持仓0.75得到2.25，源组平仓后账户仍有peer的0.75，源Risk分配归零。
- cash/equity从9850.05/10075.05变为10001.60/10076.60；按cash计算Sizer为49，不把权益当作现金。正常账户快照与Broker投影相同。
- peer绑定源组路由被拒绝且没有新增provider调用。源两条CLOSE分别RESOLVED/FILLED；真实RPC、持久订单/成交/费用与风险回执对应；重放不重复派发。
- 持久组为CLOSED/零剩余敞口，预留和预算RELEASED。生产解析器验证close proof的组/腿/源订单/成交数量/费用/回执digest及HELD→RELEASED。
- Reservation Host使用标准reserve/query/release注册，透传观察证明实际只query；借用原seed accepted store，先关闭并join，再清理共享Risk资源。所有正常会话、runtime、Host及资源关闭断言通过；两Risk owner线程关闭、join，无cleanup错误，资源cleanup一次。
- 本节点未调用Monitor；未对冲告警复用[第169轮](../arb06-group-monitor-relay-20261016/root-adjudication.md)已验收的真实三Host→durable `group.residual`、幂等poll及只读源证据，不重复执行。其既有多portfolio journal限制和失败路径单测缺口继续披露，不扩张为跨域告警证明。

## 提交与来源

Backtrader现有dev提交 `20bce86db71d465f11047f23cbb67f7bd4e56af5`，精确三个路径：两测试/fixture及短验收记录；父为884c3d4d。test为64645字节 / `e27c347236d2135a6561cb1c0fcef717459e959c6a366b364a4a010e4d900d6a`，fixture为62737字节 / `42ec7e52e44d9a796fb27d6f194175b988535a643e9d5c14a16a9ea2411a8640`。postcommit核对blob等于验收字节、空index及无关改动不变。
R10 SDK执行HEAD为954b78b1，BT执行HEAD为884c3d4d；其余package HEAD、继承R18支持变体和全部来源hash保存在spec。R10 spec SHA-256 `6943c79545e3a8db25e875adb0b98bbdcad4b6da40c31db3c1324ce9c6c7af0b`；stdout `87d724fefd7793be1692e837fa93e4c842d067c40af6598230b3f60fa31b4f0f`。
R7/R8/R9的失败原件保留：不合法binding字段/alias、金额字符串尾零、Side枚举大小写oracle；最终修正保留类型及业务数值断言。独立静态审查GO；R10通过后未为交付重复QA。SDK 9e45ce18的授权PLAN修订号修复及3751e31c的PREPARED查询修复均为已提交依赖。

并行CTP source轮已单独提交子包4d0f564及SDK954b78b1：Root 7 passed/2.24秒/无warning，183 pins/3HEAD不变。精确feed构造失败时尚未创建native owner才能正常关闭；custom未知状态保留，Gateway未知owner guard不改。该轮不操作原SimNow会话，也不新增VENUE PASS。

## 仍未验收的7项

| 用例 | 阻塞与下一动作 |
|---|---|
| ARB05、DIST01、DIST02 | 用户暂无双机环境；保留真实双机场景，环境可用后验收。 |
| DIM07、LOAD01 | 可用内存本轮观察约8.68GB，低于10.603GB门槛；DIM07 R5与LOAD01 R12静态准备已完成。执行前重核HEAD/资源；先120秒LOAD pilot，再1800秒完整负载。未运行、不算PASS。 |
| VENUE01、VENUE02 | retained SimNow工具会话6414的限制未解决；先按既有约束完成正常恢复，再按已授权范围进行账户验收。本轮未操作它，也未停止Qoder。 |

用户允许受阻项先跳过；以上暂缓不等于完成或取消。完整目标仍未完成。
