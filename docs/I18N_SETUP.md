# 多语言支持设置指南

bt_api_py 文档支持中英文双语。

## 启用步骤

### 1. 安装 i18n 插件

```bash
pip install mkdocs-static-i18n

```bash

### 2. 更新 mkdocs.yml

在 `plugins` 部分添加 i18n 插件：

```yaml
plugins:

  - search:

      lang:

        - ja
        - en
  - i18n:

      default_language: zh
      languages:

        - locale: zh

          default: true
          name: 中文
          build: true

        - locale: en

          name: English
          build: true
      docs_structure: suffix
      default_translations_only: false

```bash

### 3. 创建英文文档

在 `docs/` 目录下创建对应的英文文件，使用 `.en.md` 后缀：

```bash
docs/
├── index.md         # 中文首页

├── index.en.md      # 英文首页

├── installation.md
├── installation.en.md
└── ...

```bash

### 4. 添加语言切换器

在 `mkdocs.yml` 的 `theme.features` 中添加：

```yaml
theme:
  features:

    - navigation.indexes
    - navigation.tabs
    - navigation.sections

```bash
Material Theme 会自动显示语言切换器。

## 翻译建议

1. **保持结构一致**: 英文文档应与中文文档保持相同的目录结构
2. **翻译代码注释**: 示例代码中的注释也要翻译
3. **更新同步**: 修改中文文档时同步更新英文版本

## 文档命名规则

| 中文文档 | 英文文档 |

|----------|----------|

| `index.md` | `index.en.md` |

| `installation.md` | `installation.en.md` |

| `binance/index.md` | `binance/index.en.md` |

## 快速翻译模板

```markdown

# Installation

Install bt_api_py using pip:

```bash
pip install bt_api_py

```bash

## Requirements

- Python 3.11 or higher

```bash

## 自动翻译辅助

可以使用以下工具辅助翻译：

- DeepL API
- Google Translate API
- GitHub Copilot

但务必人工校对技术术语和代码示例。
