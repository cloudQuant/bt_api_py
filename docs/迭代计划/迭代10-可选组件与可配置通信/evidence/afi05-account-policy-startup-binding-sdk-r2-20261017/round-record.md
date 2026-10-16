# AFI05 account-policy startup binding — SDK selected-unit round

日期：2026-10-17
仓库：`D:\bt_api_py`，`dev`；基线 HEAD：`295c499bc068d6c507f554721d0b73b02d451856`。

本轮只选取 account-policy startup binding source overlay 与前两项 Risk admission tests。Source patch 为 `.codex-tmp/afi05-account-policy-head-overlay-r1/selected.patch`，9,616 B / SHA-256 `da99ab91ef43c1016f9fe6f36267c89b74d85827843e192271d94238526ff19c`；`git apply --cached --check` 对本轮基线通过。patch 只涉及 `bt_api_py/service_components.py` 的受信 account-owner policy resolver/精确 gate binding 代码。

原 R1 记录固定的 9,162 B test 源码（SHA-256 `b0c2fd9ce7f981411175eab76d6bfed4ffe07d2e9d12a2792d2fbd2e043dcc49`）在现存导出和 QA 目录中未找到，不能核实旧 AST 或声称字节恢复。本轮从当前三测试文件只选取原 review 所描述的两个测试，形成 9,221 B candidate / SHA-256 `6cd1136d28affef843f8a5a6cd4f5cc883cde242530cef35ae707071bb5660dc`；它与 R1 pin 相差 59 B，不覆盖也不改写 R1 记录。原 live 文件仍保留第三个启动确认测试作为未提交工作。

仅运行上述两个 candidate 节点：Python 3.11.5，`python -m pytest -q --noconftest -c .codex-tmp/afi05-account-policy-startup-unit-r2/pytest.ini --import-mode=importlib --junitxml=.codex-tmp/afi05-account-policy-startup-unit-r2/junit.xml .codex-tmp/afi05-account-policy-startup-unit-r2/test_service_risk_policy_admission_resolution.py`。结果 exit 0，2 passed / 0 skipped / 0 errors / 0 failures，0.93 s；JUnit 为 547 B / SHA-256 `7b2f2bd69c33a95f007bce245c45d1ac303cfd284cafe29cec4328cccc076e9c`。测试使用临时 SQLite、合成 account target 和本地 DurableRiskGate，覆盖逐账户已确认版本准入与错误账户 scope 拒绝。

证据级别：`AUTHOR_LOCAL_SYNTHETIC_UNIT_ONLY`。运行使用现有 dev checkout 的 Python import roots；`bt_api_py/service_components.py` 与 Risk 子仓库当时包含其他未提交 lane 变更，因此此结果不等于 fixed-source export、Host/HTTP、真实账户或正式 AFI05 acceptance。提交哈希由本轮 post-commit 记录补充；本记录不会把旧 R1 pin 变更为本轮 candidate。
