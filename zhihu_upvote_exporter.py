#!/usr/bin/env python3
"""
知乎用户点赞内容导出工具（CLI版）

使用知乎个人动态 API 获取指定用户的所有点赞回答和文章，
以 Markdown 或 HTML（富文本）格式保存到本地，支持增量爬取和 Cookie/扫码/Cookie 文件三种登录方式。

特性：
  - 支持 Cookie 命令行粘贴、从文件读取或 Playwright 自动扫码登录
  - 增量爬取：首次全量，后续只抓取新增内容
  - 支持 Markdown 纯文本或 HTML 富文本两种导出格式
  - 自动生成带日期和标题的文件，按「赞同的回答」和「赞同的文章」分目录存储
  - 限流自动重试、网络异常容错
  - 支持 --config / -cfg 从 YAML 或 JSON 配置文件加载参数
  - 基于 aiohttp 异步并发抓取，通过 --concurrency 控制并发数

用法:
  python zhihu_upvote_exporter.py <用户主页URL>
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --cookie "your_cookie"
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -C cookie.txt
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --visible
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --limit 10
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --no-incremental
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -f html
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --config config.yaml
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --concurrency 10

命名说明:
  本工具后缀使用 "exporter" 而非 CLI 指南推荐的通用后缀（extractor/converter 等），
  因为其核心功能是将知乎平台数据"导出"为本地 Markdown 文件，"exporter" 更准确地
  描述了数据导出的语义，且已形成用户使用习惯。

依赖:
  pip install aiohttp
  （可选，YAML 配置文件）pip install pyyaml
  （可选，如需自动扫码登录）pip install playwright && playwright install chromium
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from html import unescape
import hashlib
import base64

# ---- 延迟导入：核心依赖 ----
try:
    import aiohttp
except ImportError:
    aiohttp = None

# ---- 延迟导入：可选依赖 ----
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


# ===================== 默认配置 =====================
DEFAULT_USER_URL = ""
DEFAULT_OUTPUT_DIR = "output"
PAGE_DELAY = 0.8   # 每页请求间隔（秒），避免触发风控
RETRY_DELAY = 5    # 被限流时重试等待秒数
MAX_RETRIES = 3    # 单页最大重试次数
DEFAULT_CONCURRENCY = 5  # 默认并发数
IMAGE_RETRIES = 2  # 图片下载/上传最大重试次数
GITEE_API_BASE = "https://gitee.com/api/v5"
# ====================================================

# 配置文件 key → argparse dest 映射
_CONFIG_KEY_MAP = {
    "cookie":         "cookie",
    "cookie_file":    "cookie_file",
    "output_dir":     "output",
    "format":         "format",
    "limit":          "limit",
    "no_incremental": "no_incremental",
    "no_scan":        "no_scan",
    "visible":        "visible",
    "concurrency":    "concurrency",
    "user_url":       "user_url",
    "download_images": "download_images",
    "image_host":     "image_host",
    "gitee_token":    "gitee_token",
    "gitee_repo":     "gitee_repo",
    "gitee_branch":   "gitee_branch",
    "gitee_path_prefix": "gitee_path_prefix",
}


def extract_username(url):
    """从用户主页 URL 提取用户名（URL Token）"""
    m = re.search(r"/people/([^/?]+)", url)
    return m.group(1) if m else None


def get_headers(referer_url):
    """构造请求头（缺 Cookie 部分，后续补充）"""
    return {
        "x-api-version": "3.0.40",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "x-requested-with": "fetch",
        "accept": "*/*",
        "referer": referer_url,
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def login_manual():
    """手动输入 Cookie"""
    print("\n[手动模式]")
    print("推荐方法：从 Network 面板一键复制")
    print("  1. 打开知乎主页（确保已登录）")
    print("  2. F12 → Network（网络）标签")
    print("  3. Ctrl+R 刷新页面")
    print("  4. 在左侧列表点击任意一个请求（比如 www.zhihu.com）")
    print("  5. 右侧找到 Request Headers → 找到 Cookie: 那一行")
    print("  6. 直接整行复制，粘贴到下方（按回车结束）：\n")
    cookie = input("Cookie: ").strip()
    if not cookie:
        print("未输入 Cookie，退出。")
        sys.exit(1)
    return cookie


def read_cookie_from_file(file_path):
    """从文件读取 Cookie（支持 name=value 每行一对，或整行 Cookie 字符串）"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]

        # 如果文件只有一行且包含分号，视为整行 Cookie 字符串
        if len(lines) == 1 and ";" in lines[0]:
            return lines[0]

        # 否则按每行 name=value 解析
        cookie_parts = []
        for line in lines:
            if "=" in line:
                name, value = line.split("=", 1)
                cookie_parts.append(f"{name.strip()}={value.strip()}")
            else:
                cookie_parts.append(line.strip())

        return "; ".join(cookie_parts)
    except Exception as e:
        print(f"读取 Cookie 文件失败: {e}")
        sys.exit(1)


def login_playwright(visible=False):
    """使用 Playwright 打开浏览器扫码登录，自动获取 Cookie"""
    if sync_playwright is None:
        print("\n[提示] 未安装 playwright，回退到手动模式。")
        print("如需自动扫码登录，请执行：pip install playwright && playwright install chromium")
        return login_manual()

    print("\n[自动扫码模式] 即将打开浏览器，请在浏览器中扫码登录知乎...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not visible)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.zhihu.com/signin", wait_until="domcontentloaded")
        print("请在弹出的浏览器窗口中扫码登录（等待最多 120 秒）...")
        try:
            page.wait_for_url("https://www.zhihu.com/**", timeout=120_000)
        except Exception:
            print("等待超时，请确认是否已完成登录。")
        time.sleep(2)
        cookies = context.cookies()
        browser.close()

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    if not cookie_str:
        print("未能获取 Cookie，请重试或使用手动模式。")
        sys.exit(1)
    print("Cookie 获取成功。")
    return cookie_str


def sanitize_filename(name):
    """去除文件名中的非法字符"""
    return re.sub(r'[\\/:*?"<>|]', "_", name)[:80]


def process_content(html_content):
    """将知乎富文本 HTML 转为 Markdown 格式（用于 md 导出）"""
    if not html_content:
        return "（无内容）"
    text = html_content
    # <b> → **text**
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text)
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text)
    # <i> → *text*
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text)
    text = re.sub(r"<em>(.*?)</em>", r"*\1*", text)
    # <br> → 换行
    text = re.sub(r"<br\s*/?>", "\n", text)
    # <p>...</p> → 段落换行
    text = re.sub(r"<p.*?>", "\n", text)
    text = re.sub(r"</p>", "\n", text)
    # <img> → 保留图片链接
    text = re.sub(
        r'<img[^>]+src="([^"]+)"[^>]*>',
        r"\n![图片](\1)\n",
        text,
    )
    # <a href> → 保留链接
    text = re.sub(
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        r"[\2](\1)",
        text,
    )
    # <blockquote> → Markdown 引用
    text = re.sub(r"<blockquote.*?>", "\n> ", text)
    text = re.sub(r"</blockquote>", "\n", text)
    # <code> → Markdown 代码
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text)
    # <pre> → 代码块
    text = re.sub(r"<pre.*?>", "\n```\n", text)
    text = re.sub(r"</pre>", "\n```\n", text)
    # 去掉其余所有 HTML 标签
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    # 清理多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ============================================================
# HTML 富文本模板
# ============================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{
    max-width: 800px;
    margin: 40px auto;
    padding: 0 20px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC",
                 "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 16px;
    line-height: 1.8;
    color: #1a1a1a;
    background: #fff;
  }}
  h1 {{ font-size: 24px; margin-bottom: 0.3em; }}
  .meta {{
    color: #8590a6;
    font-size: 14px;
    margin: 0.5em 0 1.5em;
    line-height: 1.8;
  }}
  .meta a {{ color: #175199; text-decoration: none; }}
  .meta a:hover {{ text-decoration: underline; }}
  .meta-item {{ display: inline-block; margin-right: 1.5em; }}
  hr.divider {{ border: none; border-top: 1px solid #eee; margin: 1.5em 0; }}
  .content img {{ max-width: 100%; height: auto; border-radius: 4px; }}
  .content blockquote {{
    border-left: 3px solid #ccc;
    padding-left: 1em;
    margin-left: 0;
    color: #646464;
  }}
  .content figure {{ margin: 1em 0; }}
  .content figcaption {{ color: #8590a6; font-size: 14px; text-align: center; }}
  .content pre {{
    background: #f6f8fa;
    padding: 1em;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 14px;
  }}
  .content code {{
    background: #f6f8fa;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 90%;
  }}
  .content pre code {{ background: none; padding: 0; }}
  .content a {{ color: #175199; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">
{meta_html}
</div>
<hr class="divider">
<div class="content">
{content_html}
</div>
</body>
</html>"""


def _build_meta_html(type_label, author_name, author_id, time_upvoted,
                     time_created, voteup_count, comment_count, answer_url,
                     question_url=None, question_title=None):
    """构建 HTML 格式的元信息块"""
    parts = []
    parts.append(f'<span class="meta-item"><strong>类型</strong>：{type_label}</span>')
    if author_name:
        parts.append(
            f'<span class="meta-item"><strong>作者</strong>：'
            f'<a href="https://www.zhihu.com/people/{author_id}">{author_name}</a></span>'
        )
    else:
        parts.append('<span class="meta-item"><strong>作者</strong>：匿名用户</span>')
    parts.append(f'<span class="meta-item"><strong>赞同时间</strong>：{time_upvoted}</span>')
    parts.append(f'<span class="meta-item"><strong>创建时间</strong>：{time_created}</span>')
    parts.append(f'<span class="meta-item"><strong>赞同数</strong>：{voteup_count}</span>')
    parts.append(f'<span class="meta-item"><strong>评论数</strong>：{comment_count}</span>')
    if answer_url:
        parts.append(f'<span class="meta-item"><a href="{answer_url}">原文链接</a></span>')
    if question_url and question_title:
        parts.append(
            f'<span class="meta-item"><strong>问题</strong>：'
            f'<a href="{question_url}">{question_title}</a></span>'
        )
    return "<br>".join(parts)


def format_html_answer(item):
    """格式化赞同回答为 HTML 富文本"""
    target = item.get("target", {})
    question = target.get("question", {})
    author = target.get("author", {})
    time_upvoted = datetime.fromtimestamp(item.get("created_time", 0))
    time_created = datetime.fromtimestamp(target.get("created_time", 0))

    title = question.get("title", "（无标题）")
    meta_html = _build_meta_html(
        type_label="赞同的回答",
        author_name=author.get("name"),
        author_id=author.get("id", ""),
        time_upvoted=time_upvoted.strftime("%Y-%m-%d %H:%M:%S"),
        time_created=time_created.strftime("%Y-%m-%d %H:%M:%S"),
        voteup_count=target.get("voteup_count", 0),
        comment_count=target.get("comment_count", 0),
        answer_url=f"https://www.zhihu.com/question/{question.get('id', '')}/answer/{target.get('id', '')}",
        question_url=f"https://www.zhihu.com/question/{question.get('id', '')}",
        question_title=title,
    )
    return HTML_TEMPLATE.format(
        title=title,
        meta_html=meta_html,
        content_html=target.get("content", "") or "（无内容）",
    )


def format_html_article(item):
    """格式化赞同文章为 HTML 富文本"""
    target = item.get("target", {})
    author = target.get("author", {})
    time_upvoted = datetime.fromtimestamp(item.get("created_time", 0))
    time_created = datetime.fromtimestamp(target.get("created", 0))

    title = target.get("title", "（无标题）")
    meta_html = _build_meta_html(
        type_label="赞同的文章",
        author_name=author.get("name"),
        author_id=author.get("id", ""),
        time_upvoted=time_upvoted.strftime("%Y-%m-%d %H:%M:%S"),
        time_created=time_created.strftime("%Y-%m-%d %H:%M:%S"),
        voteup_count=target.get("voteup_count", 0),
        comment_count=target.get("comment_count", 0),
        answer_url=target.get("url", ""),
    )
    return HTML_TEMPLATE.format(
        title=title,
        meta_html=meta_html,
        content_html=target.get("content", "") or "（无内容）",
    )


def format_md_answer(item):
    """格式化赞同回答为 Markdown"""
    target = item.get("target", {})
    question = target.get("question", {})
    author = target.get("author", {})
    time_upvoted = datetime.fromtimestamp(item.get("created_time", 0))
    time_created = datetime.fromtimestamp(target.get("created_time", 0))

    lines = []
    lines.append(f"# {question.get('title', '（无标题）')}")
    lines.append("")
    lines.append(f"- **类型**：赞同的回答")
    lines.append(f"- **作者**：{author.get('name', '匿名用户')}")
    lines.append(f"- **作者主页**：https://www.zhihu.com/people/{author.get('id', '')}")
    lines.append(f"- **赞同时间**：{time_upvoted.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **回答创建时间**：{time_created.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **赞同数**：{target.get('voteup_count', 0)}")
    lines.append(f"- **评论数**：{target.get('comment_count', 0)}")
    lines.append(
        f"- **原文链接**：https://www.zhihu.com/question/{question.get('id', '')}/answer/{target.get('id', '')}"
    )
    lines.append(f"- **问题链接**：https://www.zhihu.com/question/{question.get('id', '')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## 问题原文")
    lines.append("")
    lines.append(f"> {question.get('title', '（无标题）')}")
    lines.append("")
    lines.append("## 回答正文")
    lines.append("")
    lines.append(process_content(target.get("content", "")))
    lines.append("")
    return "\n".join(lines)


def format_md_article(item):
    """格式化赞同文章为 Markdown"""
    target = item.get("target", {})
    author = target.get("author", {})
    time_upvoted = datetime.fromtimestamp(item.get("created_time", 0))
    time_created = datetime.fromtimestamp(target.get("created", 0))

    lines = []
    lines.append(f"# {target.get('title', '（无标题）')}")
    lines.append("")
    lines.append(f"- **类型**：赞同的文章")
    lines.append(f"- **作者**：{author.get('name', '匿名用户')}")
    lines.append(f"- **作者主页**：https://www.zhihu.com/people/{author.get('id', '')}")
    lines.append(f"- **赞同时间**：{time_upvoted.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **文章创建时间**：{time_created.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **赞同数**：{target.get('voteup_count', 0)}")
    lines.append(f"- **评论数**：{target.get('comment_count', 0)}")
    lines.append(f"- **原文链接**：{target.get('url', '')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## 文章标题")
    lines.append("")
    lines.append(f"> {target.get('title', '（无标题）')}")
    lines.append("")
    lines.append("## 文章正文")
    lines.append("")
    lines.append(process_content(target.get("content", "")))
    lines.append("")
    return "\n".join(lines)


def build_activity_url(username):
    """构造知乎个人动态 API 地址"""
    return (
        f"https://www.zhihu.com/api/v3/moments/{username}/activities"
        f"?limit=7&desktop=true"
    )


def load_progress(progress_file, fmt):
    """加载指定导出格式的爬取进度"""
    default = {"last_activity_time": 0, "answer_count": 0, "article_count": 0}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                all_progress = json.load(f)
            return all_progress.get(fmt, default)
        except Exception:
            pass
    return default


def save_progress(progress_file, fmt, last_activity_time, answer_count, article_count):
    """保存指定导出格式的爬取进度（读写合并，不覆盖其他格式的记录）"""
    entry = {
        "last_activity_time": last_activity_time,
        "answer_count": answer_count,
        "article_count": article_count,
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    all_progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                all_progress = json.load(f)
        except Exception:
            pass
    all_progress[fmt] = entry
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(all_progress, f, ensure_ascii=False, indent=2)


# ============================================================
# 配置文件加载
# ============================================================

def _load_yaml_config(path):
    """加载 YAML 配置文件（延迟导入 PyYAML，自动修复非法转义字符）"""
    try:
        import yaml
    except ImportError:
        print("错误：读取 YAML 配置文件需要 PyYAML 库")
        print("请运行：pip install pyyaml")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # 修复 Cookie / Token 等字段中可能出现的非法转义（如 \B → \\B）
    content = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', content)
    return yaml.safe_load(content)


def _load_json_config(path):
    """加载 JSON 配置文件，自动修复 Cookie 等字段中的非法转义字符"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # 将不在合法 JSON 转义字符列表中的 \ 替换为 \\（如 \x → \\x；保留已正确转义的 \\）
    content = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', content)
    return json.loads(content)


def load_config(path):
    """根据文件扩展名加载 YAML 或 JSON 配置文件"""
    if not os.path.exists(path):
        print(f"错误：配置文件不存在：{path}")
        sys.exit(1)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        return _load_yaml_config(path)
    elif ext == ".json":
        return _load_json_config(path)
    else:
        print(f"不支持的配置文件格式：{ext}，仅支持 .yaml、.yml 和 .json")
        sys.exit(1)


def _extract_config_path_from_argv():
    """从 sys.argv 中手动提取 --config / -cfg 参数值（在 argparse 解析之前）"""
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--config", "-cfg"):
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
                return sys.argv[i + 1]
        elif arg.startswith("--config="):
            return arg.split("=", 1)[1]
        elif arg.startswith("-cfg="):
            return arg.split("=", 1)[1]
    return None


def apply_config_defaults(parser, config_path):
    """读取配置文件并将参数映射为 argparse 默认值（命令行参数优先级更高）"""
    config = load_config(config_path)
    mapped = {}
    for key, value in config.items():
        dest = _CONFIG_KEY_MAP.get(key, key)
        mapped[dest] = value

    # 对 store_true 类型的参数特殊处理：配置文件中的 bool 值直接作为默认值
    parser.set_defaults(**mapped)
    return config


# ============================================================
# 异步 HTTP 请求工具
# ============================================================

async def _fetch_page(session, url, headers, semaphore):
    """
    异步获取单页数据，带重试和 429 限流处理。

    Returns:
        (dict | None): 解析后的 JSON 数据，失败返回 None
    """
    for retry in range(MAX_RETRIES):
        try:
            async with semaphore:
                async with session.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        try:
                            return await resp.json()
                        except Exception:
                            return None
                    if resp.status == 429:
                        print(f"(被限流，等待 {RETRY_DELAY}s)", end=" ", flush=True)
                        await asyncio.sleep(RETRY_DELAY)
                    else:
                        print(f"(HTTP {resp.status})", end=" ", flush=True)
                        await asyncio.sleep(RETRY_DELAY)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"(网络错误: {e})", end=" ", flush=True)
            await asyncio.sleep(RETRY_DELAY)
    return None


async def _verify_cookie(session, username, headers):
    """
    验证 Cookie 是否有效。

    Returns:
        bool: Cookie 有效返回 True
    """
    try:
        async with session.get(
            build_activity_url(username), headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return "error" not in data
            return False
    except Exception:
        return False


# ============================================================
# 图片下载与托管（local / gitee）
# ============================================================

# 已知的知乎图片域名
_ZHIHU_IMG_DOMAINS = ("pic.zhimg.com", "picx.zhimg.com", "pic1.zhimg.com",
                       "pic2.zhimg.com", "pic3.zhimg.com", "pic4.zhimg.com",
                       "pica.zhimg.com", "picb.zhimg.com", "picc.zhimg.com",
                       "picd.zhimg.com")


def _zhihu_img_clean_url(url):
    """去除知乎 CDN 尺寸后缀（如 _720w），获取更高清的原始图 URL"""
    url = re.sub(r'/(80|100|200|400|720|1080|1440|1600|2000|2500)/', '/', url)
    url = re.sub(r'_(r|hd|720w|1080w|1440w|2000w)(\.\w+)', r'\2', url)
    return url


def _get_img_ext(url_or_ct):
    """从 URL 或 Content-Type 推断图片扩展名，默认 .jpg"""
    m = re.search(r'\.(\w{3,4})(?:\?|$)', url_or_ct)
    if m:
        ext = m.group(1).lower()
        if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"):
            return ".jpg" if ext == "jpeg" else f".{ext}"
    ct = url_or_ct.lower() if "/" in url_or_ct else ""
    if "image/png" in ct:
        return ".png"
    if "image/gif" in ct:
        return ".gif"
    if "image/webp" in ct:
        return ".webp"
    return ".jpg"


def extract_image_urls(content):
    """从 HTML/Markdown 内容中提取所有图片 URL（去重，只保留知乎图床链接）"""
    urls = set()
    for m in re.finditer(r'<img[^>]+src="([^"]+)"', content):
        url = m.group(1)
        if any(d in url for d in _ZHIHU_IMG_DOMAINS):
            urls.add(url)
    for m in re.finditer(r'!\[.*?\]\(([^)]+)\)', content):
        url = m.group(1)
        if any(d in url for d in _ZHIHU_IMG_DOMAINS):
            urls.add(url)
    return list(urls)


def _get_image_name(url):
    """根据 URL 生成唯一文件名：12 位 MD5 + 扩展名"""
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    ext = _get_img_ext(url)
    return f"{url_hash}{ext}"


async def _download_image_bytes(session, url, headers):
    """下载单张图片，返回 (image_name, bytes) 或 (None, None)"""
    img_name = _get_image_name(url)
    for retry in range(IMAGE_RETRIES):
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    ct = resp.headers.get("Content-Type", "")
                    if ct and "image" in ct:
                        ext = _get_img_ext(ct)
                        base = os.path.splitext(img_name)[0]
                        img_name = f"{base}{ext}"
                    return img_name, data
                if resp.status in (403, 404):
                    return None, None
        except Exception:
            await asyncio.sleep(1)
    return None, None


async def _upload_to_gitee(session, token, repo, branch, file_path, content_bytes, semaphore):
    """上传文件到 Gitee 仓库，返回 raw URL 或 None"""
    owner, repo_name = repo.split("/", 1)
    api_url = f"{GITEE_API_BASE}/repos/{owner}/{repo_name}/contents/{file_path}"
    payload = {
        "access_token": token,
        "content": base64.b64encode(content_bytes).decode(),
        "message": f"upload: {os.path.basename(file_path)}",
        "branch": branch,
    }
    for retry in range(IMAGE_RETRIES):
        try:
            async with semaphore:
                async with session.post(api_url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 201:
                        return f"https://gitee.com/{owner}/{repo_name}/raw/{branch}/{file_path}"
                    data = await resp.json()
                    if resp.status == 400 and "already exists" in str(data):
                        return f"https://gitee.com/{owner}/{repo_name}/raw/{branch}/{file_path}"
                    await asyncio.sleep(2)
        except Exception:
            await asyncio.sleep(2)
    return None


async def _process_images(session, image_config, item_content, target_id):
    """
    处理单条内容的图片：下载 → 替换 URL。

    Args:
        session: aiohttp.ClientSession
        image_config: dict，包含 host / local_dir / concurrency / gitee_* 等
        item_content: dict，key 为格式名（"md"/"html"），value 为内容字符串
        target_id: 条目 ID，用于构建子目录 / 远端路径

    Returns:
        dict: 同样格式的 dict，内容中的图片 URL 已被替换
    """
    if not image_config or not image_config.get("enabled"):
        return item_content

    first_content = next(iter(item_content.values()), "")
    urls = extract_image_urls(first_content)
    if not urls:
        return item_content

    host = image_config.get("host", "local")
    semaphore = asyncio.Semaphore(image_config.get("concurrency", 3))
    headers = {"Referer": "https://www.zhihu.com/", "User-Agent": "Mozilla/5.0"}

    # 并发下载所有图片
    download_tasks = [_download_image_bytes(session, _zhihu_img_clean_url(u), headers)
                      for u in urls]
    results = await asyncio.gather(*download_tasks, return_exceptions=True)

    # 构建 cleaned_url → 替换路径 映射
    url_map = {}

    if host == "gitee":
        token = image_config.get("gitee_token")
        repo = image_config.get("gitee_repo")
        branch = image_config.get("gitee_branch", "master")
        path_prefix = image_config.get("gitee_path_prefix", "").strip("/")
        gitee_sem = asyncio.Semaphore(2)

        upload_tasks = []
        upload_entries = []
        for orig_url, result in zip(urls, results):
            if isinstance(result, Exception) or result == (None, None):
                continue
            img_name, img_data = result
            if img_data is None:
                continue
            remote = f"{path_prefix}/{target_id}/{img_name}" if path_prefix else f"{target_id}/{img_name}"
            upload_tasks.append(_upload_to_gitee(session, token, repo, branch, remote, img_data, gitee_sem))
            upload_entries.append(_zhihu_img_clean_url(orig_url))

        upload_results = await asyncio.gather(*upload_tasks, return_exceptions=True)
        for cleaned_url, raw_url in zip(upload_entries, upload_results):
            if raw_url and not isinstance(raw_url, Exception):
                url_map[cleaned_url] = raw_url
    else:
        # 本地模式：在线程池中写文件
        # local_dir 是图片库的绝对根目录（如 output/<user>/assets/images）
        images_root = image_config.get("local_dir")
        local_full = os.path.join(images_root, target_id)
        os.makedirs(local_full, exist_ok=True)
        loop = asyncio.get_running_loop()

        # 生成相对路径片段：../assets/images/<target_id>/<img_name>
        rel_base = os.path.join("..", "assets", "images", target_id)

        def _write_local():
            written = {}
            for orig_url, result in zip(urls, results):
                if isinstance(result, Exception) or result == (None, None):
                    continue
                img_name, img_data = result
                if img_data is None:
                    continue
                filepath = os.path.join(local_full, img_name)
                with open(filepath, "wb") as fh:
                    fh.write(img_data)
                rel_path = os.path.join(rel_base, img_name).replace("\\", "/")
                written[_zhihu_img_clean_url(orig_url)] = rel_path
            return written

        url_map = await loop.run_in_executor(None, _write_local)

    if not url_map:
        return item_content

    # 替换所有格式内容中的图片 URL
    replaced = {}
    for fmt_key, content in item_content.items():
        for orig_url in urls:
            clean = _zhihu_img_clean_url(orig_url)
            if clean in url_map:
                replacement = url_map[clean]
                content = content.replace(f'src="{orig_url}"', f'src="{replacement}"')
                content = content.replace(f'({orig_url})', f'({replacement})')
        replaced[fmt_key] = content

    return replaced


# ============================================================
# 单条目异步写入
# ============================================================

def _write_item_sync(filepath, content):
    """同步写入单个文件（在线程池中执行），使用临时文件 + 重命名保证原子性"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    # 使用临时文件写入，再原子 rename，避免并发覆盖
    tmp_path = filepath + ".tmp." + str(os.getpid())
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    try:
        os.replace(tmp_path, filepath)  # Windows 上原子替换
        return True
    except OSError:
        # 目标已存在则清理临时文件并跳过
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False


async def _process_item(session, item, format_configs, active_formats, image_config=None):
    """
    异步处理单个点赞条目：为每种格式生成内容、处理图片并并发写入文件。

    Args:
        session: aiohttp.ClientSession
        image_config: 图片处理配置，None 或 {"enabled": True, "host": "local"|"gitee", "local_dir": ..., ...}
    """
    action = item.get("action_text", "")
    if action not in ("赞同了回答", "赞同了文章"):
        return {"new": False, "is_answer": False}

    is_answer = (action == "赞同了回答")
    target = item.get("target", {})
    activity_time = item.get("created_time", 0)
    time_upvoted = datetime.fromtimestamp(activity_time)
    date_str = time_upvoted.strftime("%Y-%m-%d")

    if is_answer:
        question = target.get("question", {})
        title = sanitize_filename(question.get("title", "无标题"))
    else:
        title = sanitize_filename(target.get("title", "无标题"))

    target_id = str(target.get("id", ""))

    # 先生成所有格式的内容
    contents = {}
    for f in active_formats:
        if is_answer:
            contents[f] = format_html_answer(item) if f == "html" else format_md_answer(item)
        else:
            contents[f] = format_html_article(item) if f == "html" else format_md_article(item)

    # 如果有图片配置，下载替换图片 URL
    if image_config and image_config.get("enabled") and contents:
        contents = await _process_images(
            session=session,
            image_config=image_config,
            item_content=contents,
            target_id=target_id,
        )

    # 收集所有需要写入的任务，按格式记录
    write_tasks = []
    format_keys = []
    for f in active_formats:
        cfg = format_configs[f]
        if is_answer:
            file_dir = cfg["answers_dir"]
        else:
            file_dir = cfg["articles_dir"]
        filename = f"{date_str}_{title}_{target_id}{cfg['ext']}"
        filepath = os.path.join(file_dir, filename)

        content = contents[f]

        # 在线程池中执行同步文件写入
        loop = asyncio.get_running_loop()
        write_tasks.append(loop.run_in_executor(None, _write_item_sync, filepath, content))
        format_keys.append(f)

    results = await asyncio.gather(*write_tasks)
    # 按格式记录是否新增
    new_per_format = {}
    for f, wrote in zip(format_keys, results):
        new_per_format[f] = bool(wrote)
    wrote_any = any(new_per_format.values())

    return {
        "new": wrote_any,
        "new_per_format": new_per_format,
        "is_answer": is_answer,
        "activity_time": activity_time,
    }


# ============================================================
# 异步核心爬取
# ============================================================

async def _crawl_async(
    username, cookie, output_dir, limit, no_incremental,
    fmt, concurrency, image_config=None,
):
    """
    异步核心爬取逻辑：使用 aiohttp 并发抓取知乎用户点赞内容。

    Args:
        username: 知乎用户名（URL Token）
        cookie: Cookie 字符串
        output_dir: 输出根目录
        limit: 限制条数（None=不限）
        no_incremental: 是否禁用增量
        fmt: 输出格式
        concurrency: 并发数
        image_config: 图片处理配置，None 或 {"enabled": bool, "host": "local"|"gitee", ...}

    Returns:
        tuple: (answer_count, article_count)
    """
    if aiohttp is None:
        print("错误：缺少必要依赖 aiohttp")
        print("请运行：pip install aiohttp")
        sys.exit(1)

    referer_url = f"https://www.zhihu.com/people/{username}"
    base_headers = get_headers(referer_url)
    base_headers["cookie"] = cookie

    async with aiohttp.ClientSession() as session:
        # --- 验证 Cookie ---
        print("\n正在验证 Cookie...")
        if not await _verify_cookie(session, username, base_headers):
            print("Cookie 验证失败，请检查 Cookie 是否有效。")
            sys.exit(1)
        print("Cookie 验证通过。")

        # --- 准备输出目录 ---
        output_root = os.path.join(output_dir, username)

        if fmt == "both":
            active_formats = ["md", "html"]
            fmt_label = "MD+HTML"
        else:
            active_formats = [fmt]
            fmt_label = fmt.upper()

        format_configs = {}
        for f in active_formats:
            if f == "md":
                cfg = {
                    "answers_dir": os.path.join(output_root, "赞同的回答"),
                    "articles_dir": os.path.join(output_root, "赞同的文章"),
                    "ext": ".md",
                }
            else:
                cfg = {
                    "answers_dir": os.path.join(output_root, "赞同的回答_html"),
                    "articles_dir": os.path.join(output_root, "赞同的文章_html"),
                    "ext": ".html",
                }
            os.makedirs(cfg["answers_dir"], exist_ok=True)
            os.makedirs(cfg["articles_dir"], exist_ok=True)
            format_configs[f] = cfg

        # --- 图片配置 ---
        if image_config and image_config.get("enabled"):
            if image_config.get("host") == "local":
                image_config["local_dir"] = os.path.join(output_root, "assets", "images")
                os.makedirs(image_config["local_dir"], exist_ok=True)
            # 图片并发数默认与爬取并发数一致
            if "concurrency" not in image_config:
                image_config["concurrency"] = concurrency
            print(f"图片处理：{image_config['host']} 模式已启用")
        else:
            image_config = None

        # --- 加载进度 ---
        progress_file = os.path.join(output_root, ".progress.json")

        if no_incremental:
            last_time = 0
            prev_answers = 0
            prev_articles = 0
            print(f"\n[全量模式] 已禁用增量，将重新抓取所有点赞内容（{fmt_label}）...")
        else:
            min_last = None
            for f in active_formats:
                p = load_progress(progress_file, f)
                pt = p.get("last_activity_time", 0)
                if min_last is None or pt < min_last:
                    min_last = pt
            last_time = min_last or 0
            ref_p = load_progress(progress_file, active_formats[0])
            prev_answers = ref_p.get("answer_count", 0)
            prev_articles = ref_p.get("article_count", 0)

            if last_time > 0:
                last_time_str = datetime.fromtimestamp(last_time).strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n[增量模式] 上次已抓取至 {last_time_str}（{prev_answers} 个回答 + {prev_articles} 篇文章）")
                print(f"将只抓取此时间之后的新点赞（{fmt_label}）...")
            else:
                print(f"\n[全量模式] 首次运行，将抓取所有点赞内容（{fmt_label}）...")

        # --- 开始抓取 ---
        # 按格式分别统计新增数量
        per_fmt_answers = {f: 0 for f in active_formats}
        per_fmt_articles = {f: 0 for f in active_formats}
        answer_count = 0   # 去重后的回答数（同一内容跨格式只计一次）
        article_count = 0
        total_items = 0
        new_latest_time = last_time

        print(f"\n开始抓取用户 [{username}] 的点赞动态...\n")

        url = build_activity_url(username)
        is_end = False
        stopped_early = False
        semaphore = asyncio.Semaphore(concurrency)
        page_num = 0

        while not is_end:
            page_num += 1
            print(f"正在获取第 {page_num} 页...", end=" ", flush=True)

            data = await _fetch_page(session, url, base_headers, semaphore)
            if data is None:
                print("\n请求失败次数过多，中止。请检查 Cookie 是否有效。")
                break

            if "error" in data:
                print(f"\nAPI 返回错误：{data.get('error', {}).get('message', '未知错误')}")
                print("请检查 Cookie 是否有效或是否被风控。")
                break

            is_end = data.get("paging", {}).get("is_end", True)
            url = data.get("paging", {}).get("next", "")
            items = data.get("data", [])

            # 筛选点赞条目
            candidates = []
            for item in items:
                action = item.get("action_text", "")
                if action not in ("赞同了回答", "赞同了文章"):
                    continue

                activity_time = item.get("created_time", 0)
                if activity_time > new_latest_time:
                    new_latest_time = activity_time

                if last_time > 0 and activity_time <= last_time:
                    stopped_early = True
                    break

                candidates.append(item)

                if limit and total_items + len(candidates) >= limit:
                    stopped_early = True
                    break

            # 并发处理本页条目
            page_new_count = 0
            if candidates:
                tasks = [_process_item(session, c, format_configs, active_formats, image_config) for c in candidates]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, Exception):
                        print(f"\n处理条目时出错：{r}")
                        continue
                    if r.get("new"):
                        page_new_count += 1
                        total_items += 1
                        # 按格式分别计数
                        new_per_fmt = r.get("new_per_format", {})
                        if r.get("is_answer"):
                            answer_count += 1
                            for f in active_formats:
                                if new_per_fmt.get(f):
                                    per_fmt_answers[f] += 1
                        else:
                            article_count += 1
                            for f in active_formats:
                                if new_per_fmt.get(f):
                                    per_fmt_articles[f] += 1

            print(f"本页新增 {page_new_count} 条（累计：{answer_count} 个回答 + {article_count} 篇文章）")

            if stopped_early:
                if limit and total_items >= limit:
                    print(f"已达到上限 ({limit} 条)，停止。")
                else:
                    print("已到达上次记录时间，停止翻页。")
                break

            if page_new_count == 0 and not is_end:
                print("本页无点赞动态，继续翻页...")

            await asyncio.sleep(PAGE_DELAY)

        # --- 保存进度（按格式独立存储真实计数）---
        for f in active_formats:
            p = load_progress(progress_file, f)
            prev_f_answers = p.get("answer_count", 0)
            prev_f_articles = p.get("article_count", 0)
            total_f_answers = prev_f_answers + per_fmt_answers[f]
            total_f_articles = prev_f_articles + per_fmt_articles[f]

            if limit and total_items >= limit:
                save_progress(progress_file, f, last_time, total_f_answers, total_f_articles)
                if f == active_formats[0]:
                    print(f"[注意] 因 --limit {limit} 截断，进度时间戳未推进，下次运行将继续从 "
                          f"{datetime.fromtimestamp(last_time).strftime('%Y-%m-%d %H:%M:%S')} 开始")
            else:
                save_progress(progress_file, f, new_latest_time, total_f_answers, total_f_articles)

        print(f"\n========== 导出完成 ==========")
        print(f"本次新增（去重）：{answer_count} 个回答 + {article_count} 篇文章")
        print(f"输出格式：{fmt_label}")
        # 按格式展示实际写入的文件数
        for f in active_formats:
            print(f"  {f.upper()} 新增：{per_fmt_answers[f]} 个回答 + {per_fmt_articles[f]} 篇文章")

        for f in active_formats:
            cfg = format_configs[f]
            print(f"  {f.upper()} 回答目录：{cfg['answers_dir']}")
            print(f"  {f.upper()} 文章目录：{cfg['articles_dir']}")

        return answer_count, article_count


# ============================================================
# Cookie 获取（同步，在 asyncio.run 之前执行）
# ============================================================

def _acquire_cookie(cookie, cookie_file, no_scan, visible):
    """
    同步获取 Cookie（支持命令行、文件、Playwright 扫码、手动输入）。

    Returns:
        str: Cookie 字符串
    """
    if cookie:
        print("\n使用提供的 Cookie...")
        return cookie
    if cookie_file:
        print(f"\n从文件读取 Cookie: {cookie_file}")
        return read_cookie_from_file(cookie_file)
    if no_scan:
        return login_manual()

    print("\n请选择 Cookie 获取方式：")
    print("  [1] 手动粘贴 Cookie（推荐，最稳定）")
    print("  [2] 自动扫码登录（需要安装 playwright）")
    choice = input("\n请输入选项 (1/2，默认 1): ").strip() or "1"
    if choice == "2":
        return login_playwright(visible=visible)
    return login_manual()


# ============================================================
# 主处理函数（向后兼容的同步包装器）
# ============================================================

def main_function(user_url, cookie=None, cookie_file=None, output_dir=DEFAULT_OUTPUT_DIR,
                  limit=None, visible=False, no_scan=False,
                  no_incremental=False, fmt="md", concurrency=DEFAULT_CONCURRENCY,
                  download_images=False, image_host="local",
                  gitee_token=None, gitee_repo=None, gitee_branch=None,
                  gitee_path_prefix=None):
    """
    主处理函数：获取知乎用户点赞内容并导出为 Markdown 或 HTML

    Args:
        user_url: 知乎用户主页 URL
        cookie: Cookie 字符串（可选）
        cookie_file: Cookie 文件路径（可选）
        output_dir: 输出根目录
        limit: 限制抓取条数（None 表示不限）
        visible: Playwright 扫码时是否显示浏览器窗口
        no_scan: 是否跳过 Playwright 扫码模式
        no_incremental: 是否跳过增量机制，全量重新抓取
        fmt: 输出格式，"md"/"html"/"both"
        concurrency: 异步并发数
        download_images: 是否下载图片到本地/图床
        image_host: 图片托管方式，"local" 或 "gitee"
        gitee_token: Gitee 个人访问令牌（image_host=gitee 时必需）
        gitee_repo: Gitee 仓库，格式 "owner/repo"（image_host=gitee 时必需）
        gitee_branch: Gitee 分支名，默认 "master"
        gitee_path_prefix: Gitee 仓库中的路径前缀（可选）

    Returns:
        tuple: (answer_count, article_count)
    """
    username = extract_username(user_url)
    if not username:
        print(f"错误：无法从 URL 提取用户名：{user_url}")
        sys.exit(1)

    print("=" * 50)
    print("  知乎点赞内容导出工具（支持增量，aiohttp 异步）")
    print("=" * 50)
    print(f"\n目标用户：{username}")
    print(f"主页链接：{user_url}")

    # --- 同步获取 Cookie（Playwright 扫码/手动输入）---
    cookie = _acquire_cookie(cookie, cookie_file, no_scan, visible)

    # --- 构建图片配置 ---
    img_cfg = None
    if download_images:
        img_cfg = {"enabled": True, "host": image_host}
        if image_host == "gitee":
            if not gitee_token or not gitee_repo:
                print("错误：Gitee 模式需要 --gitee-token 和 --gitee-repo")
                sys.exit(1)
            img_cfg["gitee_token"] = gitee_token
            img_cfg["gitee_repo"] = gitee_repo
            img_cfg["gitee_branch"] = gitee_branch or "master"
            if gitee_path_prefix:
                img_cfg["gitee_path_prefix"] = gitee_path_prefix

    # --- 进入异步抓取 ---
    return asyncio.run(_crawl_async(
        username=username,
        cookie=cookie,
        output_dir=output_dir,
        limit=limit,
        no_incremental=no_incremental,
        fmt=fmt,
        concurrency=concurrency,
        image_config=img_cfg,
    ))


# ============================================================
# CLI 入口
# ============================================================

def main():
    """CLI入口函数"""
    parser = argparse.ArgumentParser(
        description="导出知乎用户的所有点赞回答和文章为本地 Markdown 文件"
                    "（支持增量爬取、Cookie/扫码双模式登录、aiohttp 异步并发、配置文件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --cookie "your_cookie"
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -o D:\\MyExports --limit 20
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --no-incremental --visible
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -f html
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --config config.yaml
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --concurrency 10
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --download-images
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --download-images --image-host gitee --gitee-token TOKEN --gitee-repo owner/repo
        """,
    )

    # ---- 输入选项 ----
    input_group = parser.add_argument_group("输入选项")
    input_group.add_argument(
        "user_url",
        nargs="?",
        default=None,
        help="知乎用户主页 URL（例如 https://www.zhihu.com/people/xxx）",
    )
    input_group.add_argument(
        "-c", "--cookie",
        default=None,
        help="知乎登录 Cookie 字符串（不提供则进入交互式登录流程）",
    )
    input_group.add_argument(
        "-C", "--cookie-file",
        default=None,
        help="从文件读取 Cookie（每行一对 name=value 或 Cookie 字符串）",
    )

    # ---- 配置文件选项 ----
    config_group = parser.add_argument_group("配置文件")
    config_group.add_argument(
        "--config", "-cfg",
        default=None,
        help="从 YAML 或 JSON 配置文件加载参数（命令行参数优先级更高）",
    )

    # ---- 输出选项 ----
    output_group = parser.add_argument_group("输出选项")
    output_group.add_argument(
        "-o", "--output",
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出根目录（默认: {DEFAULT_OUTPUT_DIR}）",
    )
    output_group.add_argument(
        "-f", "--format",
        choices=["md", "html", "both"],
        default="md",
        help="输出格式：md=Markdown 纯文本，html=HTML 富文本（保留原始排版、图片、样式），"
             "both=同时导出两种（默认: md）",
    )
    output_group.add_argument(
        "-l", "--limit",
        type=int,
        default=None,
        help="限制抓取条数，达到上限后停止（默认: 不限制）",
    )
    output_group.add_argument(
        "--download-images",
        action="store_true",
        help="下载文章/回答中的图片到本地或上传到 Gitee 图床",
    )
    output_group.add_argument(
        "--image-host",
        choices=["local", "gitee"],
        default="local",
        help="图片托管方式：local=下载到本地 assets/images/，gitee=上传到 Gitee 仓库（默认: local）",
    )
    output_group.add_argument(
        "--gitee-token",
        default=None,
        help="Gitee 个人访问令牌（--image-host gitee 时必需）",
    )
    output_group.add_argument(
        "--gitee-repo",
        default=None,
        help="Gitee 仓库名，格式 owner/repo（--image-host gitee 时必需）",
    )
    output_group.add_argument(
        "--gitee-branch",
        default="master",
        help="Gitee 仓库分支名（默认: master）",
    )
    output_group.add_argument(
        "--gitee-path-prefix",
        default=None,
        help="Gitee 仓库中存放图片的路径前缀（可选）",
    )

    # ---- 抓取选项 ----
    scrape_group = parser.add_argument_group("抓取选项")
    scrape_group.add_argument(
        "--no-incremental",
        action="store_true",
        help="禁用增量模式，全量重新抓取所有点赞内容（默认: 启用增量）",
    )
    scrape_group.add_argument(
        "--no-scan",
        action="store_true",
        help="跳过 Playwright 扫码模式，仅使用手动粘贴 Cookie（默认: 优先扫码）",
    )
    scrape_group.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"异步并发数，控制同时处理的条目数量（默认: {DEFAULT_CONCURRENCY}）",
    )

    # ---- 浏览器选项 ----
    browser_group = parser.add_argument_group("浏览器选项（Playwright 扫码时生效）")
    browser_group.add_argument(
        "--visible",
        action="store_true",
        help="扫码登录时显示浏览器窗口（默认: 后台运行）",
    )

    # ---- 配置文件加载（在 argparse 解析之前，先用配置文件设默认值）----
    config_path = _extract_config_path_from_argv()
    if config_path:
        print(f"加载配置文件：{config_path}")
        apply_config_defaults(parser, config_path)

    args = parser.parse_args()

    if not args.user_url:
        print("错误：未提供用户主页 URL。请通过命令行参数或配置文件（user_url）指定。")
        sys.exit(1)

    try:
        main_function(
            user_url=args.user_url,
            cookie=args.cookie,
            cookie_file=args.cookie_file,
            output_dir=args.output,
            limit=args.limit,
            visible=args.visible,
            no_scan=args.no_scan,
            no_incremental=args.no_incremental,
            fmt=args.format,
            concurrency=args.concurrency,
            download_images=args.download_images,
            image_host=args.image_host,
            gitee_token=args.gitee_token,
            gitee_repo=args.gitee_repo,
            gitee_branch=args.gitee_branch,
            gitee_path_prefix=args.gitee_path_prefix,
        )
    except KeyboardInterrupt:
        print("\n\n操作被用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n处理失败: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
