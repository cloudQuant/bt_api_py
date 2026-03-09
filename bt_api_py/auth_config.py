"""认证配置 — 统一管理不同交易所的认证方式
加密货币交易所使用 API Key，CTP 使用 Broker/User/Password，IB 使用 TWS 连接参数.
"""

from typing import Any

__all__ = [
    "AuthConfig",
    "CryptoAuthConfig",
    "CtpAuthConfig",
    "IbAuthConfig",
    "IbWebAuthConfig",
]


class AuthConfig:
    """认证配置基类."""

    def __init__(self, exchange: str, asset_type: str = "SWAP", **kwargs: Any) -> None:
        """Initialize authentication configuration.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX").
            asset_type: Asset type (e.g., "SWAP", "SPOT", "FUTURE"). Defaults to "SWAP".
            **kwargs: Additional keyword arguments.

        """
        self.exchange = exchange
        self.asset_type = asset_type

    def get_exchange_name(self) -> str:
        """Return exchange identifier with asset type.

        Returns:
            Exchange identifier in format "EXCHANGE___ASSET_TYPE" (e.g., "BINANCE___SWAP").

        """
        return f"{self.exchange}___{self.asset_type}"

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary.

        Returns:
            Dictionary containing all non-private attributes for feed constructor.

        """
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


class CryptoAuthConfig(AuthConfig):
    """加密货币交易所认证配置（Binance, OKX 等）."""

    def __init__(
        self,
        exchange: str,
        asset_type: str = "SWAP",
        public_key: str | None = None,
        private_key: str | None = None,
        passphrase: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize cryptocurrency exchange authentication configuration.

        Args:
            exchange: Exchange identifier (e.g., "BINANCE", "OKX").
            asset_type: Asset type (e.g., "SWAP", "SPOT"). Defaults to "SWAP".
            public_key: API public key.
            private_key: API private key.
            passphrase: API passphrase (required for OKX).
            **kwargs: Additional keyword arguments passed to parent.

        """
        super().__init__(exchange, asset_type, **kwargs)
        self.public_key = public_key
        self.private_key = private_key
        self.passphrase = passphrase


class CtpAuthConfig(AuthConfig):
    """CTP 认证配置."""

    def __init__(
        self,
        exchange: str = "CTP",
        asset_type: str = "FUTURE",
        broker_id: str = "",
        user_id: str = "",
        password: str = "",
        auth_code: str = "",
        app_id: str = "",
        md_front: str = "",
        td_front: str = "",
        product_info: str = "",
        **kwargs: Any,
    ) -> None:
        """Initialize CTP authentication configuration.

        Args:
            exchange: Exchange identifier. Defaults to "CTP".
            asset_type: Asset type. Defaults to "FUTURE".
            broker_id: Broker identifier.
            user_id: User identifier.
            password: User password.
            auth_code: Authentication code.
            app_id: Application identifier.
            md_front: Market data front address (e.g., "tcp://180.168.146.187:10131").
            td_front: Trading front address (e.g., "tcp://180.168.146.187:10130").
            product_info: Product information.
            **kwargs: Additional keyword arguments passed to parent.

        """
        super().__init__(exchange, asset_type, **kwargs)
        self.broker_id = broker_id
        self.user_id = user_id
        self.password = password
        self.auth_code = auth_code
        self.app_id = app_id
        self.md_front = md_front
        self.td_front = td_front
        self.product_info = product_info


class IbAuthConfig(AuthConfig):
    """Interactive Brokers 认证配置."""

    def __init__(
        self,
        exchange: str = "IB",
        asset_type: str = "STK",
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        **kwargs: Any,
    ) -> None:
        """Initialize Interactive Brokers authentication configuration.

        Args:
            exchange: Exchange identifier. Defaults to "IB".
            asset_type: Asset type (e.g., "STK", "FUT", "OPT"). Defaults to "STK".
            host: TWS/Gateway host address. Defaults to "127.0.0.1".
            port: TWS/Gateway port (TWS=7497, Gateway=4001). Defaults to 7497.
            client_id: Client ID for connection. Defaults to 1.
            **kwargs: Additional keyword arguments passed to parent.

        """
        super().__init__(exchange, asset_type, **kwargs)
        self.host = host
        self.port = port
        self.client_id = client_id


class IbWebAuthConfig(AuthConfig):
    """Interactive Brokers Web API 认证配置.

    支持三种认证方式:
      1. Client Portal Gateway (个人客户): base_url="https://localhost:5000", verify_ssl=False
      2. OAuth 2.0 (机构客户): base_url="https://api.interactivebrokers.com",
         需提供 client_id + private_key_path 或 access_token
      3. 浏览器 Cookie (个人客户增强认证): 从已登录浏览器提取 cookie，
         用于访问需要浏览器会话的端点 (/portfolio/{id}/summary 等)
    """

    def __init__(
        self,
        exchange: str = "IB_WEB",
        asset_type: str = "STK",
        base_url: str = "https://localhost:5000",
        account_id: str | None = None,
        access_token: str | None = None,
        client_id: str | None = None,
        private_key_path: str | None = None,
        verify_ssl: bool = False,
        proxies: dict[str, str] | None = None,
        timeout: int = 10,
        cookies: dict[str, str] | None = None,
        cookie_source: str | None = None,
        cookie_browser: str = "chrome",
        cookie_path: str = "/sso",
        **kwargs: Any,
    ) -> None:
        """Initialize Interactive Brokers Web API authentication configuration.

        Supports three authentication methods:
            1. Client Portal Gateway (individual): base_url="https://localhost:5000", verify_ssl=False
            2. OAuth 2.0 (institutional): base_url="https://api.interactivebrokers.com",
               requires client_id + private_key_path or access_token
            3. Browser Cookie (enhanced auth): extract cookies from logged-in browser,
               used for endpoints requiring browser session (/portfolio/{id}/summary etc.)

        Args:
            exchange: Exchange identifier. Defaults to "IB_WEB".
            asset_type: Asset type (e.g., "STK", "FUT"). Defaults to "STK".
            base_url: API base URL. Defaults to "https://localhost:5000".
            account_id: IBKR account ID (e.g., "U1234567").
            access_token: OAuth 2.0 access token.
            client_id: OAuth 2.0 client ID.
            private_key_path: OAuth 2.0 private key file path.
            verify_ssl: Whether to verify SSL (set False for Gateway). Defaults to False.
            proxies: HTTP proxy configuration.
            timeout: Request timeout in seconds. Defaults to 10.
            cookies: Cookie dictionary.
            cookie_source: Cookie source configuration.
            cookie_browser: Browser type (chrome, firefox, etc.). Defaults to "chrome".
            cookie_path: Cookie path. Defaults to "/sso".
            **kwargs: Additional keyword arguments passed to parent.

        """
        super().__init__(exchange, asset_type, **kwargs)
        self.base_url = base_url
        self.account_id = account_id
        self.access_token = access_token
        self.client_id = client_id
        self.private_key_path = private_key_path
        self.verify_ssl = verify_ssl
        self.proxies = proxies
        self.timeout = timeout
        self.cookies = cookies
        self.cookie_source = cookie_source
        self.cookie_browser = cookie_browser
        self.cookie_path = cookie_path
