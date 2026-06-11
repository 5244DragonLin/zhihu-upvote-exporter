# Zhihu Upvote Exporter

知乎点赞内容导出工具。基于 aiohttp 异步并发抓取，将知乎用户的所有点赞回答和文章导出为本地 Markdown / HTML 文件，支持增量爬取、多格式输出、多种登录方式、YAML/JSON 配置文件。

## 为什么需要这个工具？

- 知乎没有官方的"导出点赞内容"功能
- 你点赞过的优质回答和文章，想回顾时只能一页页翻
- 万一哪天内容被删除或账号出问题，收藏夹里的好东西就没了
- 想把点赞内容搬到本地归档、做知识管理？只能一篇篇手动复制

**Zhihu Upvote Exporter 解决这些问题**：输入用户主页链接，自动爬取所有点赞回答和文章，每条内容保存为一个格式优美的 Markdown 或 HTML 文件。

## ⭐亮点

- 点赞内容导出：导出用户点赞过的回答和文章，而非自己创作的内容
- 双格式输出：Markdown 纯文本 + HTML 富文本（保留原始排版、图片、样式），可同时导出
- 增量爬取：记录上次抓取时间，下次仅抓取新点赞，不重复下载
- 异步并发：aiohttp 异步抓取，`--concurrency` 控制并发数，速度大幅提升
- 配置文件：支持 YAML / JSON 配置文件，命令行参数与配置文件无感融合
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

##  🚀快速开始

### 1. 克隆项目

```bash
git clone https://github.com/5244DragonLin/zhihu-upvote-exporter.git
cd zhihu-upvote-exporter
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
# 如需自动扫码登录，再安装 Playwright
pip install playwright && playwright install chromium
# 如需 YAML 配置文件支持
pip install pyyaml
```

### 3. 运行

```bash
# 导出指定用户的点赞内容（进入交互式登录）
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx

# 使用 Cookie 字符串直接运行
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -c "your_cookie"

# HTML 富文本格式导出
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -f html

# 同时导出 MD 和 HTML
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -f both

# 限制抓取 20 条，指定并发数为 8
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --limit 20 --concurrency 8

# 全量重新抓取
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --no-incremental

# 使用配置文件
python zhihu_upvote_exporter.py --config config.yaml
```

##  ⌨️CLI 模式

```
python zhihu_upvote_exporter.py [用户主页URL] [选项]
```

### 输入选项

| 参数 | 说明 |
|------|------|
| `user_url` | 知乎用户主页 URL（例如 `https://www.zhihu.com/people/xxx`），**必填** |
| `-c, --cookie` | 知乎登录 Cookie 字符串 |
| `-C, --cookie-file` | 从文件读取 Cookie（每行一对 `name=value` 或完整 Cookie 字符串） |

### 输出选项

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-o, --output` | 输出根目录 | `output` |
| `-f, --format` | 输出格式：`md` / `html` / `both` | `md` |
| `-l, --limit` | 限制抓取条数，达到上限后停止 | 不限制 |
| `--download-images` | 下载图片到本地或上传到图床 | 不下载 |
| `--image-host` | 图片托管方式：`local` / `gitee` | `local` |
| `--gitee-token` | Gitee 个人访问令牌（image-host=gitee 时必需） | — |
| `--gitee-repo` | Gitee 仓库，格式 `owner/repo`（image-host=gitee 时必需） | — |
| `--gitee-branch` | Gitee 仓库分支名 | `master` |
| `--gitee-path-prefix` | Gitee 仓库中的路径前缀 | — |

### 抓取选项

| 参数 | 说明 |
|------|------|
| `--no-incremental` | 禁用增量模式，全量重新抓取所有点赞内容 |
| `--no-scan` | 跳过 Playwright 扫码模式，仅使用手动粘贴 Cookie |
| `--concurrency` | 异步并发数，控制 aiohttp 同时请求数（默认 5） |
| `--config, -cfg` | 从 YAML 或 JSON 配置文件加载参数 |

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

### 图片下载

使用 `--download-images` 可将回答/文章中的图片下载到本地或上传到 Gitee 图床，避免知乎图床链接失效导致图片无法访问。

**本地模式**（默认）：

```bash
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --download-images
```

图片保存到 `output/<用户名>/assets/images/<条目ID>/` 目录，内容中的图片链接自动替换为相对路径 `../assets/images/<条目ID>/<图片名>`。

**Gitee 图床模式**：

```bash
python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx \
    --download-images --image-host gitee \
    --gitee-token YOUR_ACCESS_TOKEN \
    --gitee-repo owner/repo
```

图片上传到 Gitee 仓库后，内容中的图片链接自动替换为 Gitee raw URL（`https://gitee.com/owner/repo/raw/branch/...`）。

> Gitee Token 获取：Gitee → 设置 → 私人令牌 → 生成新令牌，勾选 `projects` 权限即可。

## 📝配置文件

通过 `--config` 可将参数写入 YAML / JSON 配置文件，避免每次都在命令行输入冗长的 Token 和 Repo。

**示例**（参考仓库内 `config.example.yaml`）：

```yaml
# 图片下载 - Gitee 图床模式
download_images: true
image_host: gitee
gitee_token: "your_gitee_personal_access_token"
gitee_repo: "your_username/your_repo"
gitee_branch: "master"
gitee_path_prefix: "zhihu-images"   # 可选
```

使用：

```bash
python zhihu_upvote_exporter.py --config config.yaml
```

配置文件中可设置的参数和 CLI 参数一一对应，CLI 传入的值优先级更高。

## 📂项目结构

```
zhihu-upvote-exporter/
├── zhihu_upvote_exporter.py   # 主脚本（单文件，aiohttp 异步并发）
├── requirements.txt           # Python 依赖
├── README.md
├── LICENSE
└── .gitignore
```

导出后的输出目录结构：

```
output/
└── <用户名>/
    ├── 赞同的回答/           # Markdown 格式回答
    ├── 赞同的文章/           # Markdown 格式文章
    ├── 赞同的回答_html/      # HTML 格式回答（-f html/both）
    ├── 赞同的文章_html/      # HTML 格式文章（-f html/both）
    ├── assets/images/        # 下载的图片（--download-images）
    └── .progress.json        # 增量进度记录
```

## ⚙️配置说明

脚本顶部 `默认配置` 区域可调整以下参数：

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `DEFAULT_OUTPUT_DIR` | 默认输出目录 | `output` |
| `PAGE_DELAY` | 每页请求间隔（秒） | `0.8` |
| `RETRY_DELAY` | 被限流时重试等待秒数 | `5` |
| `MAX_RETRIES` | 单页最大重试次数 | `3` |

## ❓️FAQ

**会不会封号？**

风险极低。程序调用知乎官方 API，行为与正常浏览一致。默认 0.8 秒翻一页，遇到 429 限流自动等待 5 秒重试。最坏情况是弹出验证码，不会直接封号。

**增量模式怎么工作的？**

每次运行后会在输出目录生成 `.progress.json`，记录最后一条点赞的时间戳。下次运行时只抓取该时间之后的新点赞。MD 和 HTML 两种格式的进度独立记录，互不干扰。

**中途中断了怎么办？**

已保存的文件不会丢失。重新运行后增量模式会从上次记录的时间继续抓取，不会重复下载已有文件。

**能不能只导出回答或只导出文章？**

当前版本同时导出回答和文章。如需过滤可在脚本中修改 `action not in ("赞同了回答", "赞同了文章")` 这一行。

**macOS / Linux 能用吗？**

完全支持。核心依赖 `aiohttp` 和 `playwright` 均为跨平台库。

## 🤝贡献

欢迎提 Issue 和 PR！以下是一些潜在的改进方向，供有兴趣贡献的同学参考：

### 已知问题 / 待改进点

- [x] **文件名冲突**：同一天点赞的同名标题内容，第二个会因文件名重复被跳过。已通过追加 `answer_id` / `article_id` 后缀解决。
- [x] **图片下载到本地**：Markdown / HTML 中的图片仅保留知乎图床链接，内容被删或图床失效即无法访问。已新增 `--download-images` 参数，支持下载到本地 `assets/images/` 或上传到 Gitee 图床（`--image-host gitee`），自动替换为本地相对路径或图床直链。
- [ ] **按类型过滤**：当前同时导出回答和文章。建议新增 `--only-answers` / `--only-articles`，按需单一类型导出。
- [ ] **关键词过滤**：新增 `--filter "关键词"`，仅导出标题或正文匹配指定关键词的点赞内容，支持知识库定向归档。
- [ ] **导出单文件合集**：新增 `--merge` 模式，将所有点赞合并输出为一个 Markdown / HTML 文件（含目录导航），方便全文搜索和阅读。
- [ ] **封面 / 头图提取**：HTML 输出中自动提取并展示文章封面图，Markdown 中则追加 `![cover](url)` 行。
- [ ] **导出统计报告**：运行结束后输出一个 `summary.md` / `summary.html`，按日期分布、作者排行、赞同数排行等进行可视化统计。
- [ ] **多账号 Cookie 轮换**：支持配置多个 Cookie 文件路径，触发风控时自动轮换，适合大量抓取场景。
- [ ] **Web UI 模式**：提供 `--serve` 启动本地 Web 界面，浏览器内输入 URL 和配置，避免命令行操作。

### 贡献流程

1. Fork 本仓库
2. 创建分支：`git checkout -b feature/your-feature`
3. 提交修改：`git commit -m "描述本次改动"`
4. 推送分支：`git push origin feature/your-feature`
5. 提交 Pull Request

有任何疑问欢迎提 Issue 讨论 😊

## 📋更新日志

### v2.1 (2026-06-10)

- 修复 `asyncio.gather` 异常丢失问题：单个条目处理失败不再导致整页结果丢弃
- 修复 `-f both` 双格式导出时进度计数虚高的 bug，改为按格式独立追踪实际写入数
- 文件写入改为临时文件 + `os.replace` 原子重命名，杜绝并发覆盖
- 修复同名标题文件冲突：文件名追加 `answer_id` / `article_id` 后缀保证唯一性
- 移除 tqdm 进度条依赖
- `requirements.txt` 改为仅依赖 aiohttp

### v2.0

- 架构升级：从 requests 同步迁移至 aiohttp 异步并发，新增 `--concurrency` 参数
- 新增 `--config` / `-cfg` 参数，支持 YAML / JSON 配置文件
- 输出格式新增 `both`，同时导出 Markdown 和 HTML
- 进度记录按格式隔离，`-f both` 时两种格式的增量进度互不干扰
- 延迟导入：aiohttp / yaml 均为懒加载，`--help` 无需安装依赖即可运行

### v1.0

- 首个版本：支持 Cookie / 扫码 / 文件三种登录方式
- Markdown 和 HTML 双格式输出
- 增量爬取机制，基于 `.progress.json` 断点续爬
- Playwright 自动扫码登录

## ☕捐赠

如果你觉得本项目帮助了你，请作者喝一杯咖啡，你的支持是作者最大的动力。本项目会持续更新。

| 支付宝 | 微信 |
|--------|------|
| ![支付宝](https://gitee.com/yhl5244/images/raw/master/donate_alipay.jpg) | ![微信](https://gitee.com/yhl5244/images/raw/master/donate_wechat.jpg) |

## 📃许可证

[MIT License](LICENSE) — 随便用，标注来源即可。
*（内容由AI生成，仅供参考）*
