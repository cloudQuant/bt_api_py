# {PACKAGE_NAME}

{PACKAGE_DESCRIPTION}

[![PyPI Version](https://img.shields.io/pypi/v/{PACKAGE_NAME}.svg)](https://pypi.org/project/{PACKAGE_NAME}/)
[![Python Versions](https://img.shields.io/pypi/pyversions/{PACKAGE_NAME}.svg)](https://pypi.org/project/{PACKAGE_NAME}/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/cloudQuant/{REPO_NAME}/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudQuant/{REPO_NAME}/actions)
[![Docs](https://readthedocs.org/projects/{DOCS_SLUG}/badge/?version=latest)](https://{DOCS_SLUG}.readthedocs.io/)

---

## {en:English|zh:中文}

### {en:Overview|zh:概述}

{en:This package provides {EXCHANGE_DESCRIPTION} for the bt_api framework. It offers a unified interface for interacting with {EXCHANGE_NAME} exchange.}
{zh:本包为 bt_api 框架提供 {EXCHANGE_DESCRIPTION}。它提供了与 {EXCHANGE_NAME} 交易所交互的统一接口。}

### {en:Features|zh:功能特点}

{FEATURES_LIST}

### {en:Installation|zh:安装}

```bash
pip install {PACKAGE_NAME}
```

{en:Or install from source:|zh:或从源码安装:}

```bash
git clone https://github.com/cloudQuant/{REPO_NAME}
cd {REPO_NAME}
pip install -e .
```

### {en:Quick Start|zh:快速开始}

```python
from {IMPORT_MODULE} import {IMPORT_CLASS}

# {en:Initialize|zh:初始化}
feed = {IMPORT_CLASS}(api_key="your_key", secret="your_secret")

# {en:Get ticker data|zh:获取行情数据}
ticker = feed.get_ticker("{SYMBOL}")
print(ticker)
```

### {en:Supported Operations|zh:支持的操作}

{SUPPORTED_OPS_TABLE}

### {en:Documentation|zh:在线文档}

| {en:Resource|zh:资源} | {en:Link|zh:链接} |
|-------------------|-----------------|
| {en:English Docs|zh:英文文档} | https://{DOCS_SLUG}.readthedocs.io/ |
| {en:Chinese Docs|zh:中文文档} | https://{DOCS_SLUG}.readthedocs.io/zh/latest/ |
| {en:GitHub Repository|zh:GitHub 仓库} | https://github.com/cloudQuant/{REPO_NAME} |
| {en:Issue Tracker|zh:问题反馈} | https://github.com/cloudQuant/{REPO_NAME}/issues |

### {en:Requirements|zh:系统要求}

- Python 3.9+
- bt_api_base >= {BASE_VERSION}

### {en:Architecture|zh:架构}

```
{PACKAGE_NAME}/
├── src/{PACKAGE_NAME}/     # {en:Source code|zh:源代码}
│   ├── containers/         # {en:Data containers|zh:数据容器}
│   ├── feeds/              # {en:API feeds|zh:API 源}
│   ├── gateway/            # {en:Gateway adapter|zh:网关适配器}
│   └── plugin.py           # {en:Plugin registration|zh:插件注册}
├── tests/                  # {en:Unit tests|zh:单元测试}
└── docs/                  # {en:Documentation|zh:文档}
```

### {en:License|zh:许可证}

MIT License - see [LICENSE](LICENSE) for details.

### {en:Support|zh:技术支持}

- {en:Report bugs via GitHub Issues|zh:通过 GitHub Issues 反馈问题}
- {en:Email: yunjinqi@gmail.com|zh:邮箱: yunjinqi@gmail.com}
