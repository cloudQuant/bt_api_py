# 启用 Giscus 评论系统

Giscus 是基于 GitHub Discussions 的评论系统，完全免费且无需数据库。

## 前置条件

1. 仓库是**公开的**
2. 已安装 [Giscus App](<https://github.com/apps/giscus)>
3. 仓库已启用 **Discussions** 功能

## 快速启用

### 1. 启用 GitHub Discussions

进入仓库设置：

```bash
Settings → General → Features → Discussions

```bash
勾选 "Discussions" 并保存。

### 2. 安装 Giscus App

访问 <https://github.com/apps/giscus> 并安装到你的仓库。

### 3. 获取配置参数

访问 <https://giscus.app，输入你的仓库信息：>

- 仓库: `cloudQuant/bt_api_py`
- 页面 ↔️ 讨论 映射: `pathname`
- Discussion 分类: `Announcements`
- 语言: `zh-CN`

获取以下参数：

- `data-repo-id`
- `data-category-id`

### 4. 更新 mkdocs.yml

将获取的参数填入 `mkdocs.yml` 的 `extra.giscus` 配置：

```yaml
extra:
  giscus:
    repo: cloudQuant/bt_api_py
    repo_id: R_kgDONUxxxx  # 替换为实际值
    category: Announcements
    category_id: DIC_kwDONUxxxx  # 替换为实际值
    mapping: pathname
    strict: 0
    reactions_enabled: 1
    emit_metadata: 0
    input_position: bottom
    theme: preferred_color_scheme
    lang: zh-CN

```bash

### 5. 重新构建

```bash
mkdocs build

```bash

## 配置选项

| 参数 | 说明 | 默认值 |

|------|------|--------|

| `mapping` | 页面映射方式 | `pathname` |

| `theme` | 主题 | `preferred_color_scheme` |

| `lang` | 语言 | `zh-CN` |

| `reactions_enabled` | 启用反应 | `1` |

| `input_position` | 输入框位置 | `bottom` |

## 主题切换

评论系统会自动跟随文档站点的明暗主题切换。

## 管理评论

所有评论存储在 GitHub Discussions 中，可以在仓库的 Discussions 页面管理。
