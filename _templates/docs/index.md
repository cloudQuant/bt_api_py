docs:
  en: |
    # {PACKAGE_NAME}

    {EXCHANGE_DESCRIPTION}

    {ENGLISH_CONTENT}

    ## Quick Start

    ```bash
    pip install {PACKAGE_NAME}
    ```

    ```python
    from {IMPORT_MODULE} import {IMPORT_CLASS}

    # Initialize
    feed = {IMPORT_CLASS}(api_key="your_key", secret="your_secret")

    # Get data
    ticker = feed.get_ticker("{SYMBOL}")
    print(ticker)
    ```

    ## Features

    {FEATURES_LIST}

    ## Documentation

    - [English Documentation](https://{DOCS_SLUG}.readthedocs.io/)
    - [Chinese Documentation](https://{DOCS_SLUG}.readthedocs.io/zh/latest/)
    - [GitHub Repository](https://github.com/cloudQuant/{REPO_NAME})

    ## Supported Operations

    | Operation | Status |
    |-----------|--------|
    {SUPPORTED_OPS_TABLE}

    ## Installation

    ```bash
    pip install {PACKAGE_NAME}
    ```

    Or install from source:

    ```bash
    git clone https://github.com/cloudQuant/{REPO_NAME}
    cd {REPO_NAME}
    pip install -e .
    ```

    ## Requirements

    - Python 3.9+
    - bt_api_base >= {BASE_VERSION}

    ## License

    MIT License

  zh: |
    # {PACKAGE_NAME_CN}

    {EXCHANGE_DESCRIPTION_CN}

    {CHINESE_CONTENT}

    ## 快速开始

    ```bash
    pip install {PACKAGE_NAME}
    ```

    ```python
    from {IMPORT_MODULE} import {IMPORT_CLASS}

    # 初始化
    feed = {IMPORT_CLASS}(api_key="your_key", secret="your_secret")

    # 获取数据
    ticker = feed.get_ticker("{SYMBOL}")
    print(ticker)
    ```

    ## 功能特点

    {FEATURES_LIST_CN}

    ## 在线文档

    - [英文文档](https://{DOCS_SLUG}.readthedocs.io/)
    - [中文文档](https://{DOCS_SLUG}.readthedocs.io/zh/latest/)
    - [GitHub 仓库](https://github.com/cloudQuant/{REPO_NAME})

    ## 支持的操作

    | 操作 | 状态 |
    |------|------|
    {SUPPORTED_OPS_TABLE}

    ## 安装

    ```bash
    pip install {PACKAGE_NAME}
    ```

    或从源码安装：

    ```bash
    git clone https://github.com/cloudQuant/{REPO_NAME}
    cd {REPO_NAME}
    pip install -e .
    ```

    ## 系统要求

    - Python 3.9+
    - bt_api_base >= {BASE_VERSION}

    ## 许可证

    MIT 许可证
