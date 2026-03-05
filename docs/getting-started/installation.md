# 安装指南

bt_api_py 支持 Python 3.11 及以上版本。

## 从 PyPI 安装

```bash
pip install bt_api_py

```

## 从源码安装

```bash
git clone <https://github.com/cloudQuant/bt_api_py>
cd bt_api_py
pip install -r requirements.txt
pip install .

```

## 安装开发依赖

如果你需要参与开发，请安装开发依赖：

```bash
pip install -r requirements-dev.txt

```

## 验证安装

安装完成后，可以通过以下命令验证：

```python
import bt_api_py
print(bt_api_py.__version__)

```

## 支持的平台

| 平台 | 架构 | 状态 |
|------|------|------|
| Linux | x86_64 | ✅ 完全支持 |
| macOS | arm64 (Apple Silicon) | ✅ 完全支持 |
| macOS | x86_64 (Intel) | ✅ 完全支持 |
| Windows | x64 | ✅ 完全支持 |

## 可选依赖

某些功能需要额外的依赖：

### WebSocket 支持

```bash
pip install websockets

```

### 异步 HTTP 支持

```bash
pip install httpx

```

### CTP 交易支持

CTP 使用 SWIG 绑定，需要编译 C++ 扩展：

```bash

# macOS

brew install swig

# Ubuntu/Debian

sudo apt-get install swig

# Windows

# 从 <https://www.swig.org/download.html> 下载安装

```
