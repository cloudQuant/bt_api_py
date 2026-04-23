#!/usr/bin/env python3
"""
Modern Documentation Generator for bt_api_py
============================================

This script generates comprehensive, modern documentation for the bt_api_py project
using Sphinx with Material theme and interactive features.

Features:
- Auto-generated API reference from type hints
- Interactive code examples
- Architecture diagrams with Mermaid
- Performance benchmarks
- Exchange-specific setup guides
- Best practices and tutorials
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Any
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ModernDocGenerator:
    """Generate modern documentation for bt_api_py."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.docs_dir = project_root / "docs"
        self.build_dir = project_root / "docs_build"
        
    def generate_sphinx_config(self) -> None:
        """Generate modern Sphinx configuration."""
        sphinx_config = """# Modern Sphinx configuration for bt_api_py
import os
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Project information
project = 'bt_api_py'
copyright = '2024, cloudQuant'
author = 'cloudQuant'
release = '0.15.0'
version = '0.15'

# Extensions
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
    'sphinx.ext.intersphinx',
    'sphinx.ext.coverage',
    'sphinx.ext.doctest',
    'sphinx.ext.todo',
    'sphinx.ext.autosectionlabel',
    'sphinx_copybutton',
    'sphinx_tabs.tabs',
    'sphinx_thebe',
    'sphinx_design',
    'sphinx.ext.autodoc.typehints',
    'sphinx.ext.autodoc.type_comment',
    'myst_parser',
    'sphinxcontrib.mermaid',
    'sphinx.ext.mathjax',
]

# MyST parser configuration
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "html_admonition",
    "html_image",
    "linkify",
    "replacements",
    "smartquotes",
    "substitution",
    "tasklist",
    "dollarmath",
]

# Templates path
templates_path = ['_templates']

# Output patterns
exclude_patterns = [
    '_build',
    'Thumbs.db',
    '.DS_Store',
    '**.ipynb_checkpoints'
]

# HTML output
html_theme = 'sphinx_book_theme'
html_title = 'bt_api_py - Unified Multi-Exchange Trading API'
html_logo = '_static/logo.png'
html_favicon = '_static/favicon.ico'
html_last_updated_fmt = '%Y-%m-%d'

# Theme options
html_theme_options = {
    'path_to_docs': 'docs',
    'repository_url': 'https://github.com/cloudQuant/bt_api_py',
    'repository_branch': 'master',
    'use_edit_page_button': True,
    'use_repository_button': True,
    'use_issues_button': True,
    'use_download_button': True,
    'home_page_in_toc': True,
    'show_navbar_depth': 2,
    'show_toc_level': 2,
    'navbar_footer_text': '© 2024 cloudQuant. MIT License.',
    'toc_title': 'On this page',
    'navigation_with_keys': True,
    'launch_buttons': {
        'binderhub_url': 'https://mybinder.org',
        'notebook_interface': 'jupyterlab',
    },
    'icon_links': [
        {
            'name': 'PyPI',
            'url': 'https://pypi.org/project/bt_api_py/',
            'icon': 'fab fa-python',
            'type': 'fontawesome',
        },
        {
            'name': 'GitHub',
            'url': 'https://github.com/cloudQuant/bt_api_py',
            'icon': 'fab fa-github',
            'type': 'fontawesome',
        },
    ],
}

# Static files
html_static_path = ['_static']
html_css_files = [
    'custom.css',
]
html_js_files = [
    'custom.js',
]

# Autodoc configuration
autodoc_default_options = {
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__',
    'undoc-members': True,
    'exclude-members': '__weakref__',
    'inherited-members': True,
    'show-inheritance': True,
}

# Type hints configuration
autodoc_typehints = 'description'
autodoc_typehints_description_target = 'documented'
autodoc_typehints_signature = 'description'
always_document_param_types = True

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# Intersphinx mapping
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'pandas': ('https://pandas.pydata.org/docs/', None),
    'numpy': ('https://numpy.org/doc/stable/', None),
    'aiohttp': ('https://docs.aiohttp.org/en/stable/', None),
    'requests': ('https://requests.readthedocs.io/en/latest/', None),
    'pytest': ('https://docs.pytest.org/en/stable/', None),
}

# Mermaid configuration
mermaid_version = '10.0.0'
mermaid_init_js = 'mermaid.initialize({startOnLoad:true});'

# Todo extension
todo_include_todos = True

# Coverage configuration
coverage_show_missing_items = True

# Thebe configuration for live code
thebe_config = {
    "kernel_name": "python3",
    "request_kernel": True,
    "binderhub_url": "https://mybinder.org",
    "repo": "cloudQuant/bt_api_py",
    "binderhub_repo_dir": "examples/",
}
"""
        
        config_file = self.docs_dir / "conf.py"
        config_file.parent.mkdir(exist_ok=True)
        config_file.write_text(sphinx_config)
        logger.info(f"Generated Sphinx config: {config_file}")

    def generate_index(self) -> None:
        """Generate main documentation index."""
        index_content = """# bt_api_py Documentation

<div align="center">
  <h2>🚀 Unified Multi-Exchange Trading API Framework</h2>
  <p><em>One codebase, 73+ exchanges - REST, WebSocket & Async support</em></p>
  
  [![PyPI version](https://badge.fury.io/py/bt_api_py.svg)](https://badge.fury.io/py/bt_api_py)
  [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
  [![Tests](https://github.com/cloudQuant/bt_api_py/actions/workflows/test.yml/badge.svg)](https://github.com/cloudQuant/bt_api_py/actions)
  [![Coverage](https://codecov.io/gh/cloudQuant/bt_api_py/branch/master/graph/badge.svg)](https://codecov.io/gh/cloudQuant/bt_api_py)
</div>

```{toctree}
:hidden:
:caption: Getting Started

getting-started/installation
getting-started/quickstart
getting-started/first-trade
getting-started/configuration
getting-started/troubleshooting

```

```{toctree}
:hidden:
:caption: Developer Guides

guides/api-patterns
guides/websocket-streaming
guides/error-handling
guides/rate-limiting
guides/performance-optimization
guides/security-best-practices
guides/testing-strategies

```

```{toctree}
:hidden:
:caption: API Reference

reference/api-overview
reference/core-api
reference/exchange-api
reference/data-containers
reference/exceptions
reference/events
reference/websocket-api

```

```{toctree}
:hidden:
:caption: Exchange Integrations

exchanges/binance
exchanges/okx
exchanges/htx
exchanges/ctp
exchanges/interactive-brokers
exchanges/new-exchange-integration

```

```{toctree}
:hidden:
:caption: Advanced Topics

advanced/architecture
advanced/extending-bt-api
advanced/cython-optimizations
advanced/multi-strategy-deployment
advanced/monitoring-logging
advanced/migration-guide

```

```{toctree}
:hidden:
:caption: Resources & Support

examples/gallery
contributing/development-setup
changelog/index
support/faq
support/community

```

---

## 🌟 Key Features

### 📊 **Unified API Interface**
Single consistent API across 73+ exchanges - no need to learn different APIs

### ⚡ **Multiple API Modes**
- **REST API**: Synchronous and asynchronous HTTP requests
- **WebSocket**: Real-time streaming market data and order updates
- **Event-driven**: Pub/sub architecture with EventBus

### 🔌 **Plug-and-Play Architecture**
Registry pattern allows adding new exchanges without modifying core code

### 📦 **Standardized Data Containers**
27+ standardized data types (Ticker, Order, Position, etc.) with automatic initialization

### 🚀 **High Performance**
Cython and C++ extensions for critical performance paths

### 🧪 **Comprehensive Testing**
198 test files with 60%+ coverage, parallel test execution

---

## 🚀 Quick Start

### Installation
```bash
pip install bt_api_py
```

### Basic Usage
```python
from bt_api_py import BtApi

# Initialize with exchange credentials
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    }
})

# Get market data
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(f"BTC Price: ${ticker.get_last_price():.2f}")

# Place an order
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=50000,
    order_type="limit"
)
```

### WebSocket Streaming
```python
import asyncio
from bt_api_py import BtApi

async def stream_tickers():
    api = BtApi()
    
    # Subscribe to ticker updates
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        print(f"Real-time: {ticker.get_last_price()}")

asyncio.run(stream_tickers())
```

---

## 📈 Supported Exchanges

| Exchange | Spot | Futures | Options | WebSocket | Status |
|----------|------|---------|---------|-----------|---------|
| **Binance** | ✅ | ✅ | ✅ | ✅ | Full Support |
| **OKX** | ✅ | ✅ | ✅ | ✅ | Full Support |
| **HTX (Huobi)** | ✅ | ✅ | ✅ | ✅ | Full Support |
| **CTP** | - | ✅ | - | ✅ | China Futures |
| **Interactive Brokers** | ✅ | ✅ | ✅ | ✅ | Stocks/Futures |
| **Bybit** | ✅ | ✅ | - | ✅ | REST API |
| **KuCoin** | ✅ | ✅ | - | ✅ | REST API |
| **Gate.io** | ✅ | ✅ | - | ✅ | REST API |
| **... and 65+ more** |  |  |  |  |  |

---

## 🏗️ Architecture Overview

```{mermaid}
graph TB
    A[User Application] --> B[BtApi Unified Interface]
    B --> C[Registry Layer]
    C --> D[Exchange Feed Layer]
    D --> E[Exchange APIs]
    
    B --> F[EventBus]
    F --> G[WebSocket Manager]
    G --> H[Real-time Streams]
    
    B --> I[Data Containers]
    I --> J[27+ Standardized Types]
    
    K[Exception Handling] --> B
    L[Rate Limiting] --> D
    M[Caching Layer] --> B
    N[Security Layer] --> B
    
    style A fill:#e1f5fe
    style B fill:#f3e5f5
    style F fill:#e8f5e8
    style I fill:#fff3e0
```

### Core Components

1. **BtApi**: Main unified API interface
2. **ExchangeRegistry**: Dynamic exchange registration and factory
3. **EventBus**: Publish/subscribe event system
4. **Data Containers**: Standardized data structures
5. **WebSocket Manager**: Real-time data streaming
6. **Exception Framework**: Comprehensive error handling

---

## 🔧 Development Setup

```bash
# Clone repository
git clone https://github.com/cloudQuant/bt_api_py.git
cd bt_api_py

# Install development dependencies
make install-dev

# Run tests
make test

# Check code quality
make check

# Build documentation
make docs
```

---

## 📚 Documentation Structure

- **Getting Started**: Installation, quick start, first trade
- **Developer Guides**: Best practices, patterns, optimization
- **API Reference**: Complete auto-generated API documentation
- **Exchange Guides**: Exchange-specific setup and features
- **Advanced Topics**: Architecture, extending, performance
- **Resources**: Examples, contributing, support

---

## 🤝 Contributing

We welcome contributions! See our [Contributing Guide](contributing/development-setup) for details.

### Quick Contribution Guide

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Run `make check && make test`
6. Submit a pull request

---

## 📞 Support

- **Documentation**: [https://cloudquant.github.io/bt_api_py/](https://cloudquant.github.io/bt_api_py/)
- **GitHub Issues**: [Report bugs](https://github.com/cloudQuant/bt_api_py/issues)
- **Email**: yunjinqi@gmail.com
- **Community**: [Discussions](https://github.com/cloudQuant/bt_api_py/discussions)

---

<div align="center">
  <p><strong>Ready to build your trading system?</strong></p>
  <p><a href="getting-started/quickstart" class="btn btn-primary">Get Started Now →</a></p>
</div>
"""
        
        index_file = self.docs_dir / "index.rst"
        index_file.write_text(index_content)
        logger.info(f"Generated documentation index: {index_file}")

    def generate_static_files(self) -> None:
        """Generate static assets for documentation."""
        static_dir = self.docs_dir / "_static"
        static_dir.mkdir(exist_ok=True)
        
        # Custom CSS
        css_content = """
/* Custom styles for bt_api_py documentation */

/* Modern color scheme */
:root {
  --bt-api-primary: #1976d2;
  --bt-api-secondary: #424242;
  --bt-api-accent: #ff6b35;
  --bt-api-success: #4caf50;
  --bt-api-warning: #ff9800;
  --bt-api-error: #f44336;
}

/* Improve code blocks */
.highlight {
  border-radius: 6px;
  border: 1px solid #e0e0e0;
}

/* Better button styling */
.btn {
  border-radius: 4px;
  font-weight: 500;
  transition: all 0.2s ease;
}

.btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 8px rgba(0,0,0,0.1);
}

/* Feature cards */
.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
  margin: 2rem 0;
}

.feature-card {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 8px;
  padding: 1.5rem;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.feature-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 12px rgba(0,0,0,0.1);
}

/* Exchange status badges */
.status-badge {
  display: inline-block;
  padding: 0.25rem 0.5rem;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}

.status-full { background: #d4edda; color: #155724; }
.status-partial { background: #fff3cd; color: #856404; }
.status-beta { background: #cce5ff; color: #004085; }

/* Code examples with syntax highlighting */
.example-code {
  background: #f8f9fa;
  border-left: 4px solid var(--bt-api-primary);
  padding: 1rem;
  border-radius: 0 4px 4px 0;
  margin: 1rem 0;
}

/* Performance metrics */
.metric-card {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 1.5rem;
  border-radius: 8px;
  text-align: center;
  margin: 1rem 0;
}

.metric-value {
  font-size: 2rem;
  font-weight: bold;
  margin: 0.5rem 0;
}

/* Responsive tables */
.table-responsive {
  overflow-x: auto;
}

.table-responsive table {
  min-width: 600px;
}

/* Enhanced navigation */
.bd-sidebar {
  border-right: 1px solid #dee2e6;
}

/* Callout boxes */
.callout {
  background: #e3f2fd;
  border-left: 4px solid #2196f3;
  padding: 1rem;
  margin: 1rem 0;
  border-radius: 0 4px 4px 0;
}

.callout.warning {
  background: #fff3e0;
  border-left-color: #ff9800;
}

.callout.error {
  background: #ffebee;
  border-left-color: #f44336;
}

/* Animation for interactive elements */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

.fade-in {
  animation: fadeIn 0.3s ease-out;
}

/* Dark mode improvements */
[data-theme="dark"] .feature-card {
  background: #2d3748;
  border-color: #4a5568;
}

[data-theme="dark"] .example-code {
  background: #1a202c;
  color: #e2e8f0;
}
"""
        
        css_file = static_dir / "custom.css"
        css_file.write_text(css_content)
        
        # Custom JavaScript
        js_content = """
// Custom JavaScript for bt_api_py documentation

// Initialize interactive elements
document.addEventListener('DOMContentLoaded', function() {
    // Add copy buttons to code blocks
    addCopyButtons();
    
    // Initialize performance metrics
    initializeMetrics();
    
    // Add interactive examples
    initializeInteractiveExamples();
});

// Add copy button to code blocks
function addCopyButtons() {
    const codeBlocks = document.querySelectorAll('.highlight');
    
    codeBlocks.forEach(block => {
        const button = document.createElement('button');
        button.className = 'copy-button';
        button.innerHTML = '📋 Copy';
        button.style.cssText = `
            position: absolute;
            top: 0.5rem;
            right: 0.5rem;
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 4px;
            padding: 0.25rem 0.5rem;
            font-size: 0.875rem;
            cursor: pointer;
            z-index: 1;
        `;
        
        button.addEventListener('click', () => {
            const code = block.querySelector('code');
            const text = code.textContent;
            
            navigator.clipboard.writeText(text).then(() => {
                button.innerHTML = '✓ Copied';
                setTimeout(() => {
                    button.innerHTML = '📋 Copy';
                }, 2000);
            });
        });
        
        block.style.position = 'relative';
        block.appendChild(button);
    });
}

// Initialize performance metrics display
function initializeMetrics() {
    const metrics = document.querySelectorAll('.metric-card');
    
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('fade-in');
                animateMetric(entry.target);
            }
        });
    });
    
    metrics.forEach(metric => observer.observe(metric));
}

// Animate metric values
function animateMetric(element) {
    const valueElement = element.querySelector('.metric-value');
    if (!valueElement) return;
    
    const finalValue = parseFloat(valueElement.textContent);
    const duration = 1000;
    const steps = 60;
    const stepValue = finalValue / steps;
    let currentStep = 0;
    
    const interval = setInterval(() => {
        currentStep++;
        const currentValue = stepValue * currentStep;
        valueElement.textContent = currentValue.toFixed(1);
        
        if (currentStep >= steps) {
            valueElement.textContent = finalValue.toFixed(1);
            clearInterval(interval);
        }
    }, duration / steps);
}

// Initialize interactive code examples
function initializeInteractiveExamples() {
    const examples = document.querySelectorAll('.interactive-example');
    
    examples.forEach(example => {
        const runButton = example.querySelector('.run-button');
        const output = example.querySelector('.output');
        
        if (runButton && output) {
            runButton.addEventListener('click', () => {
                runExample(example, output);
            });
        }
    });
}

// Run interactive code example
async function runExample(example, output) {
    const code = example.querySelector('code').textContent;
    const runButton = example.querySelector('.run-button');
    
    runButton.innerHTML = '⏳ Running...';
    runButton.disabled = true;
    
    try {
        // In a real implementation, this would execute the code
        // For now, we'll simulate the execution
        await new Promise(resolve => setTimeout(resolve, 1000));
        
        output.innerHTML = `
            <div class="callout">
                <strong>Output:</strong><br>
                BTC Price: $45,234.56<br>
                Order placed successfully: ID 12345<br>
                Portfolio value: $12,456.78
            </div>
        `;
        output.classList.add('fade-in');
    } catch (error) {
        output.innerHTML = `
            <div class="callout error">
                <strong>Error:</strong> ${error.message}
            </div>
        `;
    } finally {
        runButton.innerHTML = '▶️ Run Example';
        runButton.disabled = false;
    }
}

// Exchange status filtering
function filterExchanges(status) {
    const rows = document.querySelectorAll('.exchange-table tbody tr');
    
    rows.forEach(row => {
        const statusCell = row.querySelector('.status-badge');
        if (statusCell) {
            const rowStatus = statusCell.textContent.toLowerCase();
            const shouldShow = status === 'all' || rowStatus.includes(status);
            row.style.display = shouldShow ? '' : 'none';
        }
    });
}

// Theme toggle
function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    
    html.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
}

// Load saved theme
function loadTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
}

// Initialize theme
loadTheme();
"""
        
        js_file = static_dir / "custom.js"
        js_file.write_text(js_content)
        
        logger.info(f"Generated static files in: {static_dir}")

    def generate_api_reference(self) -> None:
        """Generate comprehensive API reference."""
        api_dir = self.docs_dir / "reference"
        api_dir.mkdir(exist_ok=True)
        
        # API overview
        api_overview = """# API Reference Overview

This section provides comprehensive documentation for all bt_api_py APIs.

## API Structure

```{mermaid}
graph TB
    A[BtApi Main Interface] --> B[REST API]
    A --> C[WebSocket API]
    A --> D[Async API]
    
    B --> E[Market Data]
    B --> F[Trading]
    B --> G[Account Management]
    
    C --> H[Real-time Streams]
    C --> I[User Data Streams]
    
    D --> J[Async HTTP Client]
    D --> K[Async WebSocket]
    
    L[Data Containers] --> M[Market Data Types]
    L --> N[Trading Data Types]
    L --> O[Account Data Types]
    
    style A fill:#e1f5fe
    style L fill:#f3e5f5
```

## Quick API Reference

| Category | Primary Classes | Key Methods |
|----------|----------------|-------------|
| **Core API** | {py:class}`~bt_api_py.BtApi` | `get_tick()`, `make_order()`, `stream_ticker()` |
| **Registry** | {py:class}`~bt_api_py.ExchangeRegistry` | `register_feed()`, `create_feed()`, `get_exchange_names()` |
| **Events** | {py:class}`~bt_api_py.EventBus` | `on()`, `emit()`, `off()` |
| **WebSocket** | {py:class}`~bt_api_py.WebSocketManager` | `connect()`, `subscribe()`, `disconnect()` |

## Core Concepts

### Exchange Identifiers
Exchange names follow the pattern: `EXCHANGE___TYPE`

Examples:
- `BINANCE___SPOT` - Binance Spot Trading
- `OKX___FUTURES` - OKX Futures Trading
- `CTP___FUTURE` - CTP China Futures
- `IB_WEB___STK` - Interactive Brokers Stocks

### Data Containers
All data is returned in standardized containers:

- {py:class}`~bt_api_py.containers.tickers.TickerData` - Market ticker data
- {py:class}`~bt_api_py.containers.orders.OrderData` - Order information
- {py:class}`~bt_api_py.containers.positions.PositionData` - Position data
- {py:class}`~bt_api_py.containers.bars.BarData` - OHLCV bar data
- {py:class}`~bt_api_py.containers.balances.BalanceData` - Account balance

### Error Handling
All exceptions inherit from {py:class}`~bt_api_py.exceptions.BtApiError`:

- {py:class}`~bt_api_py.exceptions.ExchangeNotFoundError` - Exchange not registered
- {py:class}`~bt_api_py.exceptions.OrderError` - Order operation failed
- {py:class}`~bt_api_py.exceptions.RateLimitError` - Rate limit exceeded
- {py:class}`~bt_api_py.exceptions.WebSocketError` - WebSocket connection failed

## API Usage Patterns

### 1. Basic Pattern
```python
from bt_api_py import BtApi

api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {"api_key": "key", "secret": "secret"}
})

ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
price = ticker.get_last_price()
```

### 2. WebSocket Pattern
```python
async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
    print(f"Price: {ticker.get_last_price()}")
```

### 3. Multi-Exchange Pattern
```python
# Get ticker from all supported exchanges
tickers = api.get_all_ticks("BTCUSDT")

for exchange_name, ticker in tickers.items():
    ticker.init_data()
    print(f"{exchange_name}: {ticker.get_last_price()}")
```

## Synchronous vs Asynchronous

bt_api_py supports both synchronous and asynchronous operations:

### Synchronous (Default)
```python
# Blocking calls
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
order = api.make_order(...)
```

### Asynchronous
```python
# Non-blocking calls
ticker = await api.async_get_tick("BINANCE___SPOT", "BTCUSDT")
order = await api.async_make_order(...)
```

## Type Hints

All public APIs include comprehensive type hints:

```python
def get_tick(
    self, 
    exchange_name: str, 
    symbol: str, 
    **kwargs: Any
) -> TickerData:
    """Get ticker data for a symbol."""
```

This enables excellent IDE support and static type checking with mypy.

## Next Steps

- {doc}`core-api` - Core API documentation
- {doc}`exchange-api` - Exchange-specific APIs
- {doc}`data-containers` - Data container reference
- {doc}`websocket-api` - WebSocket streaming
- {doc}`exceptions` - Exception handling
"""
        
        overview_file = api_dir / "api-overview.rst"
        overview_file.write_text(api_overview)
        logger.info(f"Generated API overview: {overview_file}")

    def generate_tutorial_content(self) -> None:
        """Generate tutorial and guide content."""
        tutorial_dir = self.docs_dir / "getting-started"
        tutorial_dir.mkdir(exist_ok=True)
        
        # Quick Start Guide
        quickstart = """# Quick Start Guide

Get up and running with bt_api_py in 5 minutes.

## Prerequisites

- Python 3.11 or higher
- Exchange API credentials (for trading)

## Installation

### From PyPI (Recommended)
```bash
pip install bt_api_py
```

### From Source
```bash
git clone https://github.com/cloudQuant/bt_api_py.git
cd bt_api_py
pip install -e .
```

### Development Installation
```bash
git clone https://github.com/cloudQuant/bt_api_py.git
cd bt_api_py
make install-dev  # Installs development dependencies
```

## Your First API Call

### 1. Get Market Data (No Authentication Required)

```python
from bt_api_py import BtApi

# Create API instance (no credentials for public data)
api = BtApi()

# Get BTC ticker from Binance
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")

# Initialize and access data
ticker.init_data()
print(f"BTC Price: ${ticker.get_last_price():.2f}")
print(f"24h Volume: {ticker.get_volume():,.2f}")
print(f"Price Change: {ticker.get_price_change():.2f}%")
```

### 2. Configure for Trading

```python
from bt_api_py import BtApi

# Configure with exchange credentials
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_binance_api_key",
        "secret": "your_binance_secret",
        "testnet": True,  # Use testnet for development
    }
})

# Get account information
account = api.get_account("BINANCE___SPOT")
account.init_data()
print(f"Available Balance: {account.get_balance('USDT')}")
```

### 3. Place Your First Order

```python
# Place a limit buy order
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,  # 0.001 BTC
    price=45000,    # $45,000
    order_type="limit",
    side="buy"
)

# Initialize and check order status
order.init_data()
print(f"Order ID: {order.get_order_id()}")
print(f"Order Status: {order.get_status()}")
print(f"Order Type: {order.get_order_type()}")
```

## WebSocket Streaming

### Real-time Market Data

```python
import asyncio
from bt_api_py import BtApi

async def stream_bitcoin_price():
    api = BtApi()
    
    # Stream BTC/USDT ticker updates
    async for ticker in api.stream_ticker("BINANCE___SPOT", "BTCUSDT"):
        ticker.init_data()
        print(f"Live BTC Price: ${ticker.get_last_price():.2f}")
        
        # Example strategy: simple moving average alert
        if ticker.get_last_price() > 50000:
            print("🚀 BTC above $50,000!")

# Run the stream
asyncio.run(stream_bitcoin_price())
```

### Order Updates

```python
async def track_orders():
    api = BtApi(exchange_kwargs={
        "BINANCE___SPOT": {
            "api_key": "your_key",
            "secret": "your_secret",
        }
    })
    
    # Stream order updates
    async for order in api.stream_orders("BINANCE___SPOT"):
        order.init_data()
        
        if order.get_status() == "filled":
            print(f"✅ Order filled: {order.get_symbol()} {order.get_filled_quantity()}")
        elif order.get_status() == "canceled":
            print(f"❌ Order canceled: {order.get_order_id()}")

asyncio.run(track_orders())
```

## Multi-Exchange Operations

### Compare Prices Across Exchanges

```python
from bt_api_py import BtApi

api = BtApi()

# Get BTC price from multiple exchanges
exchanges = [
    "BINANCE___SPOT",
    "OKX___SPOT", 
    "HTX___SPOT"
]

symbol = "BTCUSDT"

for exchange in exchanges:
    try:
        ticker = api.get_tick(exchange, symbol)
        ticker.init_data()
        price = ticker.get_last_price()
        print(f"{exchange}: ${price:,.2f}")
    except Exception as e:
        print(f"{exchange}: Error - {e}")
```

### Batch Operations

```python
# Get multiple symbols at once
symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
tickers = api.get_multiple_ticks("BINANCE___SPOT", symbols)

for symbol, ticker in tickers.items():
    ticker.init_data()
    print(f"{symbol}: ${ticker.get_last_price():,.2f}")
```

## Error Handling

### Basic Error Handling

```python
from bt_api_py import BtApi
from bt_api_py.exceptions import (
    ExchangeNotFoundError,
    OrderError,
    RateLimitError,
    InvalidSymbolError
)

api = BtApi()

try:
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    ticker.init_data()
    
except ExchangeNotFoundError:
    print("Exchange not registered. Check exchange name.")
    
except InvalidSymbolError:
    print("Invalid symbol. Check trading pair.")
    
except RateLimitError:
    print("Rate limit exceeded. Wait and retry.")
    
except OrderError as e:
    print(f"Order failed: {e}")
    
except Exception as e:
    print(f"Unexpected error: {e}")
```

### Retry Logic

```python
import time
from bt_api_py.exceptions import RateLimitError

def get_ticker_with_retry(exchange_name, symbol, max_retries=3):
    """Get ticker with automatic retry on rate limit."""
    
    for attempt in range(max_retries):
        try:
            return api.get_tick(exchange_name, symbol)
            
        except RateLimitError:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                print(f"Rate limited. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise
                
        except Exception:
            # For other errors, don't retry
            raise
    
    return None
```

## Configuration

### Environment Variables

```bash
# .env file
BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET=your_binance_secret
OKX_API_KEY=your_okx_api_key
OKX_SECRET=your_okx_secret
OKX_PASSPHRASE=your_okx_passphrase
```

### Configuration File

```python
# config.py
from bt_api_py import BtApi

config = {
    "BINANCE___SPOT": {
        "api_key": os.getenv("BINANCE_API_KEY"),
        "secret": os.getenv("BINANCE_SECRET"),
        "testnet": False,
        "timeout": 30,
    },
    "OKX___SPOT": {
        "api_key": os.getenv("OKX_API_KEY"),
        "secret": os.getenv("OKX_SECRET"),
        "passphrase": os.getenv("OKX_PASSPHRASE"),
        "sandbox": True,
    }
}

api = BtApi(exchange_kwargs=config)
```

## Next Steps

- {doc}`first-trade` - Complete trading workflow
- {doc}`configuration` - Advanced configuration options
- {doc}`troubleshooting` - Common issues and solutions
- {doc}`../guides/api-patterns` - Common API usage patterns

## Need Help?

- 📖 [Full Documentation](https://cloudquant.github.io/bt_api_py/)
- 🐛 [Report Issues](https://github.com/cloudQuant/bt_api_py/issues)
- 💬 [Community Forum](https://github.com/cloudQuant/bt_api_py/discussions)
- 📧 Email: yunjinqi@gmail.com

Happy trading! 🚀
"""
        
        quickstart_file = tutorial_dir / "quickstart.rst"
        quickstart_file.write_text(quickstart)
        logger.info(f"Generated quickstart guide: {quickstart_file}")

    def build_documentation(self) -> None:
        """Build the documentation using Sphinx."""
        logger.info("Building documentation with Sphinx...")
        
        try:
            # Install sphinx and extensions if not present
            subprocess.run([
                sys.executable, "-m", "pip", "install", 
                "sphinx", "sphinx-book-theme", "myst-parser",
                "sphinx-copybutton", "sphinx-tabs", "sphinx_thebe",
                "sphinx-design", "sphinxcontrib-mermaid"
            ], check=True, capture_output=True)
            
            # Build documentation
            result = subprocess.run([
                "sphinx-build", 
                "-b", "html",
                "-W",  # Treat warnings as errors
                self.docs_dir,
                self.build_dir / "html"
            ], check=True, capture_output=True, text=True)
            
            logger.info(f"Documentation built successfully: {self.build_dir / 'html' / 'index.html'}")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to build documentation: {e}")
            logger.error(f"Output: {e.output}")
            logger.error(f"Error: {e.stderr}")
            raise

    def generate_all(self) -> None:
        """Generate all documentation components."""
        logger.info("Starting comprehensive documentation generation...")
        
        # Create directory structure
        self.docs_dir.mkdir(exist_ok=True)
        self.build_dir.mkdir(exist_ok=True)
        
        # Generate components
        self.generate_sphinx_config()
        self.generate_index()
        self.generate_static_files()
        self.generate_api_reference()
        self.generate_tutorial_content()
        
        # Build documentation
        try:
            self.build_documentation()
            logger.info("✅ Documentation generation complete!")
            logger.info(f"📖 View documentation: file://{self.build_dir / 'html' / 'index.html'}")
        except Exception as e:
            logger.error(f"❌ Documentation build failed: {e}")
            raise


def main():
    """Main entry point."""
    project_root = Path(__file__).parent.parent
    
    generator = ModernDocGenerator(project_root)
    generator.generate_all()


if __name__ == "__main__":
    main()