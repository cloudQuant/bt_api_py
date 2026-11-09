# Gateway 本机 Web 宿主（迭代 63）

本例由 `bt_api_py.gateway_web_local_host` 启动本机 Web 适配器，并通过独立 CURVE 客户端连接已有 Gateway。它不会启动交易 Gateway、读取 CTP 凭据或授予 Gateway 权限。每个浏览器操作仍由远端 Registry 检查精确主体、范围和操作；不要复用权限高于操作员的 CURVE 身份。

1. 将 `gateway-web-local.example.json` 复制到宿主机绝对路径的受保护文件，替换所有 `REPLACE` 占位符。文件必须由当前用户拥有、权限为 `0600` 或更严格，路径中不得有符号链接；该内置检查当前仅支持 POSIX。此文件含 CURVE 客户端私钥和 Web 签名密钥，禁止放进版本库、日志或公开配置。
2. 为每个主体生成独立的 32 字节随机登录令牌并用 64 位小写十六进制表示，将该令牌的 ASCII 字节 SHA-256 值写入 `login_token_sha256`，通过安全渠道交付原令牌。该令牌是高熵随机值，不是人选密码。每个操作员使用自己的主体、actor 和客户端密钥；服务公钥须独立校验。`receipt_key_base64` 为至少 32 字节随机密钥的 Base64，需持久保护；轮换时更新 `receipt_key_id`，旧凭据需重新核查。
3. 在 Gateway 侧为对应 CURVE 公钥与身份配置精确 scope 和实际需要的 operation grant。示例操作只涵盖查看和 FREEZE；需要其他操作时分别配置。没有 `direct.control.identity`、相应读取和写入授权时，Web 功能不可用。账户、策略、权限和交易就绪不能由本文件证明。
4. 安装本 SDK、Gateway Web extra 和 ZMQ transport，构建并打包 Web 静态资源。运行 `conda run -n base python -m bt_api_py.gateway_web_local_host --config /absolute/protected/path.json`，在同一主机访问 `http://127.0.0.1:8000/login` 输入令牌。登录后会话令牌保存在当前标签页的 sessionStorage，并仅通过同源 API 请求的 `X-Gateway-Session` 头发送，不使用按主机而非端口隔离的 cookie；会话最长 30 分钟，访问 `/logout` 撤销当前标签页会话。sessionStorage 可被同源脚本读取，同源 XSS、恶意浏览器扩展或本机账户失陷仍可窃取令牌；保护浏览器、静态资源和本机用户会话。不要代理到公网。

宿主在相关请求时检查受保护文件的内容、文件身份、权限和变更时间；检测到不同或读取失败后锁定全部浏览器会话，恢复旧文件也不会重新启用。该方法不是文件事件的可靠订阅，不能保证观察到所有短暂变化：文件系统时间精度、特权操作和竞态均可能影响检测。真正撤权必须在 Gateway Registry 撤销对应主体/密钥授权，并按 Gateway 的授权栅栏生效；文件检查不是原子撤权栅栏。宿主启动时某个主体无法完成真实 Gateway 注册会整体拒绝启动，关闭异常不会声称资源已释放。

当前内置宿主只装配 `application.direct_write` 控制端口，没有账户快照读取端口；账户快照接口返回授权不可用，不以配置冒充账户数据。增加账户视图需要实际 Gateway 的只读主体、覆盖范围和相应授权。Windows 文件 ACL、多浏览器安全、安装发行及真实账户行为尚待单独验证。
