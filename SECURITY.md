# Security Policy（安全策略）

**语言**: [English](#english) | [中文](#中文)

<a id="english"></a>
## Reporting a Vulnerability

**Do NOT open a public issue for security problems. Never post API keys,
secrets, account IDs, order details, or exploit details in public.**

### Preferred channel: GitHub Private Vulnerability Reporting

Once enabled by the repository admin, use
**Security → Report a vulnerability** on
<https://github.com/cloudQuant/bt_api_py/security/advisories/new>.

> Status (2026-08-23): Private Vulnerability Reporting is **not yet enabled**
> on this repository (tracked by decision gate D5). Until it is enabled, use
> the email channel below.

### Fallback channel: encrypted email

- **Contact**: yunjinqi@gmail.com
- Include: affected version/commit, exchange or module affected
  (e.g. `BINANCE___SPOT`, `forwarding`, `ctp`), impact assessment, and a
  minimal reproduction. Attach proof-of-concept privately; do not paste
  credentials.
- **Response SLA**: first acknowledgment within 3 business days; status update
  within 10 business days. (SLA pending formal owner sign-off — see D5.)

### Scope

In scope:

- Credential handling and leakage paths (API keys, tokens, session files)
- Order routing, cancellation, and idempotency flaws that could cause
  unintended real-money actions
- The `bt_api_py.forwarding` gateway (authentication, authorization,
  transport), including ZeroMQ endpoints
- Injection, deserialization, and SSRF issues in REST/WebSocket adapters
- Release/supply-chain integrity (PyPI publishing path)

Out of scope:

- Vulnerabilities in the exchanges themselves — report to the exchange
- Issues requiring leaked credentials that the user exposed themselves
- Missing features

### Coordinated disclosure

We ask for up to 90 days before public disclosure while a fix and release are
prepared. We credit reporters by default; tell us if you prefer to remain
anonymous.

<a id="中文"></a>
## 报告漏洞（中文）

**不要为安全问题开公开 issue。绝不在公开渠道张贴 API 密钥、账户信息、订单
详情或可利用细节。**

- **首选通道**：仓库管理员启用 GitHub Private Vulnerability Reporting 后，
  使用 Security → Report a vulnerability（当前状态：未启用，见决策门 D5）。
- **备用通道**：邮件 yunjinqi@gmail.com。请包含受影响版本/提交、涉及的交易所
  或模块、影响评估与最小复现；PoC 私下附件，不要粘贴凭据。
- **响应承诺**：3 个工作日内首次确认；10 个工作日内给出状态更新。
- **处理范围**：凭据处理与泄漏路径；可能导致非预期真实下单/撤单的订单路由与
  幂等缺陷；`bt_api_py.forwarding` 网关（认证、授权、ZeroMQ 传输）；适配器中的
  注入/反序列化/SSRF；发布与供应链完整性。交易所自身的漏洞请向对应交易所报告。

## 历史提示

2026-08-23 的基线核查确认 git 历史中曾短暂提交过 `keys/` 目录下的会话密钥文件
（详见 `docs/governance/baseline-2026-08-23.md`）。**任何从旧版本或历史检出获取
的密钥都应视为已泄露并立即轮换。**
