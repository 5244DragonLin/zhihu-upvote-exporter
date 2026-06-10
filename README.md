# Zhihu Upvote Exporter

知乎点赞内容导出工具。将知乎用户的所有点赞回答和文章导出为本地 Markdown / HTML 文件，支持增量爬取、多格式输出、多种登录方式。

## 为什么需要这个工具？

- 知乎没有官方的"导出点赞内容"功能
- 你点赞过的优质回答和文章，想回顾时只能一页页翻
- 万一哪天内容被删除或账号出问题，收藏夹里的好东西就没了
- 想把点赞内容搬到本地归档、做知识管理？只能一篇篇手动复制

**Zhihu Upvote Exporter 解决这些问题**：输入用户主页链接，自动爬取所有点赞回答和文章，每条内容保存为一个格式优美的 Markdown 或 HTML 文件。

## 亮点

- 点赞内容导出：导出用户点赞过的回答和文章，而非自己创作的内容
- 双格式输出：Markdown 纯文本 + HTML 富文本（保留原始排版、图片、样式），可同时导出
- 增量爬取：记录上次抓取时间，下次仅抓取新点赞，不重复下载
- 多种登录方式：Cookie 字符串 / Cookie 文件 / Playwright 扫码登录，灵活选择
- 拟人化策略：请求间隔 + 限流重试，降低风控风险
- 分目录存储：按用户 → 内容类型 → 格式自动整理到子文件夹
- 条数控制：`--limit` 限制抓取条数，适合测试或按需导出

## 效果预览

**CLI 运行效果**

```
[增量模式] 上次已抓取至 2026-06-09 15:30:00（42 个回答 + 8 篇文章）
将只抓取此时间之后的新点赞（MD+HTML）...

开始抓取用户 [xxx] 的点赞动态...

正在获取第 1 页... 本页新增 5 条（累计：5 个回答 + 0 篇文章）
正在获取第 2 页... 本页新增 3 条（累计：7 个回答 + 1 篇文章）
正在获取第 3 页... 本页新增 0 条，已到达上次记录时间，停止翻页。

========== 导出完成 ==========
本次新增：7 个回答 + 1 篇文章
输出格式：MD+HTML
  MD 回答目录：output\xxx\赞同的回答
  MD 文章目录：output\xxx\赞同的文章
  HTML 回答目录：output\xxx\赞同的回答_html
  HTML 文章目录：output\xxx\赞同的文章_html
```

**输出目录结构**

```
output/
└── 用户名/
    ├── 赞同的回答/
    │   ├── 2026-06-09_如何评价某某事件.md
    │   └── ...
    ├── 赞同的文章/
    │   ├── 2026-06-08_深度学习入门指南.md
    │   └── ...
    ├── 赞同的回答_html/
    │   ├── 2026-06-09_如何评价某某事件.html
    │   └── ...
    ├── 赞同的文章_html/
    │   ├── 2026-06-08_深度学习入门指南.html
    │   └── ...
    └── .progress.json          # 增量进度记录（按格式隔离）
```

**输出的 Markdown 文件**

```markdown
# [如何评价某某事件？](https://www.zhihu.com/question/xxx/answer/xxx)

**作者名** / 赞同于 2026-06-09 15:30:00

> 问题：[如何评价某某事件？](https://www.zhihu.com/question/xxx)

---

回答正文内容，包含 **加粗**、*斜体*、
![图片](https://pic.zhimg.com/xxx.jpg) 和 [链接](url) ...
```

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/5244DragonLin/zhihu-upvote-exporter.git
cd zhihu-upvote-exporter
```

### 2. 安装依赖

```bash
pip install requests playwright
playwright install chromium
```

### 3. 运行

```bash
# 导出指定用户的点赞内容
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx

# HTML 富文本格式导出
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -f html

# 同时导出 MD 和 HTML
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -f both

# 限制抓取 20 条
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --limit 20

# 全量重新抓取
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --no-incremental
```

首次运行会在终端提示登录方式（扫码 / 粘贴 Cookie），登录一次后 Cookie 可复用。

## CLI 模式

```
python zhihu_upvote_exporter.py [用户主页URL] [选项]
```

### 输入选项

| 参数 | 说明 |
|------|------|
| `user_url` | 知乎用户主页 URL（例如 `https://www.zhihu.com/people/xxx`） |
| `-c, --cookie` | 知乎登录 Cookie 字符串 |
| `-C, --cookie-file` | 从文件读取 Cookie（每行一对 `name=value` 或完整 Cookie 字符串） |

### 输出选项

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-o, --output` | 输出根目录 | `output` |
| `-f, --format` | 输出格式：`md` / `html` / `both` | `md` |
| `-l, --limit` | 限制抓取条数，达到上限后停止 | 不限制 |

### 抓取选项

| 参数 | 说明 |
|------|------|
| `--no-incremental` | 禁用增量模式，全量重新抓取所有点赞内容 |
| `--no-scan` | 跳过 Playwright 扫码模式，仅使用手动粘贴 Cookie |

### 浏览器选项（扫码时生效）

| 参数 | 说明 |
|------|------|
| `--visible` | 扫码登录时显示浏览器窗口（默认后台运行） |

### Cookie 文件格式

支持两种格式：

```
# 格式一：完整 Cookie 字符串（单行）
z_c0=xxx; d_c0=xxx; _zap=xxx

# 格式二：逐行 name=value
z_c0=xxx
d_c0=xxx
_zap=xxx
```

## 项目结构

```
zhihu-upvote-exporter/
├── zhihu_upvote_exporter.py   # 主脚本（单文件，零依赖其他模块）
├── README.md
├── LICENSE
└── .gitignore
```

## 配置说明

脚本顶部 `默认配置` 区域可调整以下参数：

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `DEFAULT_OUTPUT_DIR` | 默认输出目录 | `output` |
| `PAGE_DELAY` | 每页请求间隔（秒） | `0.8` |
| `RETRY_DELAY` | 被限流时重试等待秒数 | `5` |
| `MAX_RETRIES` | 单页最大重试次数 | `3` |

## FAQ

**会不会封号？**

风险极低。程序通过 requests 调用知乎官方 API，行为与正常浏览一致。默认 0.8 秒翻一页，遇到 429 限流自动等待 5 秒重试。最坏情况是弹出验证码，不会直接封号。

**增量模式怎么工作的？**

每次运行后会在输出目录生成 `.progress.json`，记录最后一条点赞的时间戳。下次运行时只抓取该时间之后的新点赞。MD 和 HTML 两种格式的进度独立记录，互不干扰。

**中途中断了怎么办？**

已保存的文件不会丢失。重新运行后增量模式会从上次记录的时间继续抓取，不会重复下载已有文件。

**能不能只导出回答或只导出文章？**

当前版本同时导出回答和文章。如需过滤可在脚本中修改 `action not in ("赞同了回答", "赞同了文章")` 这一行。

**macOS / Linux 能用吗？**

完全支持。核心依赖 `requests` 和 `playwright` 均为跨平台库。

## 贡献

欢迎提 Issue 和 PR。

## 许可证

[MIT License](LICENSE) — 随便用，标注来源即可。
*（内容由AI生成，仅供参考）*
