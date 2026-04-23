# 认证配置

不同交易所使用不同的认证方式，bt_api_py 通过统一的认证配置类封装各交易所的连接参数。

## 认证类层次结构

```
AuthConfig (基类)
├── CryptoAuthConfig   # Binance、OKX 等加密货币交易所
├── CtpAuthConfig      # 中国期货 CTP 协议
├── IbAuthConfig       # Interactive Brokers TWS/Gateway
└── IbWebAuthConfig    # Interactive Brokers Web API
```

## 使用示例

=== "Binance / OKX"

    ```python
    exchange_kwargs = {
        "BINANCE___SPOT": {
            "api_key": "your_api_key",
            "secret": "your_secret",
            "testnet": True,
        },
        "OKX___SWAP": {
            "api_key": "your_api_key",
            "secret": "your_secret",
            "passphrase": "your_passphrase",
        },
    }
    ```

=== "CTP 期货"

    ```python
    from bt_api_py import CtpAuthConfig

    exchange_kwargs = {
        "CTP___FUTURE": {
            "auth_config": CtpAuthConfig(
                broker_id="9999",
                user_id="your_user_id",
                password="your_password",
                md_front="tcp://180.168.146.187:10211",
                td_front="tcp://180.168.146.187:10201",
            )
        }
    }
    ```

=== "Interactive Brokers Web"

    ```python
    from bt_api_py import IbWebAuthConfig

    exchange_kwargs = {
        "IB_WEB___STK": {
            "auth_config": IbWebAuthConfig(
                account_id="U1234567",
                base_url="https://localhost:5000",
                verify_ssl=False,
            )
        }
    }
    ```

---

<!--
::: bt_api_py.auth_config
    options:
      show_root_heading: false
      show_source: false
      members_order: source
      heading_level: 2
      show_if_no_docstring: true
      filters:
        - "!^_"
-->
