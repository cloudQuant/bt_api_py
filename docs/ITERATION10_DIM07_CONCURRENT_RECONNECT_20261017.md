# DIM07 四进程并发重连覆盖（2026-10-17）

在 bt_api_py 现有 dev、父提交 7de45d7bed98f49622163733324fb8e13df2d7cb 上交付本轮；实际提交 SHA 见本文件 Git 历史。只追加测试，不改生产代码。初始合成 owner 完成一次 connect/read 后退出，保留 durable active guard；四个独立重连进程同时竞争，全部拒绝且至少一个要求 reconciliation，波前后 guard generation 相同，provider 调用仍仅 connect/read 各一次，配额 grant 不增加。

Root 唯一 focused QA：1 passed in 1.58s，exit 0，无 warning、stderr 空；24 个来源 pin 与 SDK/Base/Gateway 三个 HEAD 前后相同。测试子进程正常退出；失败路径另有逐子进程清理与错误保留。独立静态审查 GO。未为交付重复 QA。

- 测试：tests/integration/test_gateway_quota_session_owner.py::test_concurrent_crash_reconnect_wave_does_not_repeat_provider_login
- 测试源码：30586 B，SHA-256 be76951f3d27bc6a419c4404560c17b308ff137993e8c72190297b6e35749837。
- Spec：7724 B，SHA-256 7334b1bf4960b0ff7628b971ddcd56b4f0e638de411d4d5a24d53678905c63f0。
- Base fb47f3dd733613494aa1d86cfd94b2d6fe70314d；Gateway 12e8de6f9552f7974a450f3217d497fb9c89e33d。
- [原始 11 件 QA 输出](evidence/iteration10-dim07-reconnect-wave-20261017/root-qa.zip)保留实际命令、runner、spec、JUnit、stdout/stderr、exit 及来源前后核对。

证据等级 LOCAL_SYNTHETIC_CONCURRENT_RECONNECT_WAVE_4_CHILD_ONLY。只有合成 provider 与本机 SQLite/真实 spawn；不接受真实 CTP 登录、retained session 6414 关闭、100 策略负载或完整 DIM07。正式计数保持 61/68。
