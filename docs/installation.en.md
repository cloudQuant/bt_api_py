# Installation Guide

bt_api_py requires Python 3.11 or higher.

## Install from PyPI

```bash
pip install bt_api_py

```bash

## Install from Source

```bash
git clone <https://github.com/cloudQuant/bt_api_py>
cd bt_api_py
pip install -r requirements.txt
pip install .

```bash

## Install Development Dependencies

For development, install additional dependencies:

```bash
pip install -r requirements-dev.txt

```bash

## Verify Installation

```python
import bt_api_py
print(bt_api_py.__version__)

```bash

## Supported Platforms

| Platform | Architecture | Status |
|----------|--------------|--------|
| Linux | x86_64 | ✅ Fully Supported |
| macOS | arm64 (Apple Silicon) | ✅ Fully Supported |
| macOS | x86_64 (Intel) | ✅ Fully Supported |
| Windows | x64 | ✅ Fully Supported |

## Optional Dependencies

Some features require additional dependencies:

### WebSocket Support

```bash
pip install websockets

```bash

### Async HTTP Support

```bash
pip install httpx

```bash

### CTP Trading Support

CTP uses SWIG bindings and requires compiling C++ extensions:

```bash

# macOS

brew install swig

# Ubuntu/Debian

sudo apt-get install swig

# Windows

# Download from <https://www.swig.org/download.html>

```bash
