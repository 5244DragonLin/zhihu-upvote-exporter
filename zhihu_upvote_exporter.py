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

用法:
  python zhihu_upvote_exporter.py <用户主页URL>
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --cookie "your_cookie"
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -C cookie.txt
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --visible
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --limit 10
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --no-incremental
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -f html

命名说明:
  本工具后缀使用 "exporter" 而非 CLI 指南推荐的通用后缀（extractor/converter 等），
  因为其核心功能是将知乎平台数据"导出"为本地 Markdown 文件，"exporter" 更准确地
  描述了数据导出的语义，且已形成用户使用习惯。

依赖:
  pip install requests
  （可选，如需自动扫码登录）pip install playwright && playwright install chromium
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from html import unescape

try:
    import requests
except ImportError:
    requests = None

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
# ====================================================


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
                # 如果某行不是 name=value 格式，尝试作为整行处理
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
# 核心处理函数
# ============================================================

def main_function(user_url, cookie=None, cookie_file=None, output_dir=DEFAULT_OUTPUT_DIR,
                  limit=None, visible=False, no_scan=False,
                  no_incremental=False, fmt="md"):
    """
    主处理函数：获取知乎用户点赞内容并导出为 Markdown 或 HTML

    Args:
        user_url: 知乎用户主页 URL
        cookie: Cookie 字符串（可选）
        cookie_file: Cookie 文件路径（可选，支持每行 name=value 或整行 Cookie 字符串）
        output_dir: 输出根目录
        limit: 限制抓取条数（None 表示不限）
        visible: Playwright 扫码时是否显示浏览器窗口
        no_scan: 是否跳过 Playwright 扫码模式
        no_incremental: 是否跳过增量机制，全量重新抓取
        fmt: 输出格式，"md" 或 "html"

    Returns:
        tuple: (answer_count, article_count)
    """
    if requests is None:
        print("错误：缺少必要依赖 requests")
        print("请运行：pip install requests")
        sys.exit(1)

    username = extract_username(user_url)
    if not username:
        print(f"错误：无法从 URL 提取用户名：{user_url}")
        sys.exit(1)

    print("=" * 50)
    print("  知乎点赞内容导出工具（支持增量）")
    print("=" * 50)
    print(f"\n目标用户：{username}")
    print(f"主页链接：{user_url}")

    # --- 获取 Cookie ---
    if cookie:
        print("\n使用提供的 Cookie...")
    elif cookie_file:
        print(f"\n从文件读取 Cookie: {cookie_file}")
        cookie = read_cookie_from_file(cookie_file)
    elif no_scan:
        cookie = login_manual()
    else:
        print("\n请选择 Cookie 获取方式：")
        print("  [1] 手动粘贴 Cookie（推荐，最稳定）")
        print("  [2] 自动扫码登录（需要安装 playwright）")
        choice = input("\n请输入选项 (1/2，默认 1): ").strip() or "1"
        if choice == "2":
            cookie = login_playwright(visible=visible)
        else:
            cookie = login_manual()

    # --- 验证 Cookie ---
    print("\n正在验证 Cookie...")
    base_headers = get_headers(f"https://www.zhihu.com/people/{username}")
    base_headers["cookie"] = cookie
    try:
        test_resp = requests.get(
            build_activity_url(username), headers=base_headers, timeout=15
        )
        if test_resp.status_code == 200 and "error" not in test_resp.json():
            print("Cookie 验证通过。")
        else:
            msg = (test_resp.json().get("error", {}).get("message", "")
                   or f"HTTP {test_resp.status_code}")
            print(f"Cookie 验证失败：{msg}")
            if not cookie:
                sys.exit(1)
            retry = input("是否重新输入 Cookie？(y/n, 默认 n): ").strip().lower()
            if retry == "y":
                cookie = login_manual()
                base_headers["cookie"] = cookie
                test_resp = requests.get(
                    build_activity_url(username), headers=base_headers, timeout=15
                )
                if test_resp.status_code != 200 or "error" in test_resp.json():
                    print("Cookie 仍然无效，退出。")
                    sys.exit(1)
            else:
                print("退出。")
                sys.exit(1)
    except Exception as e:
        print(f"网络错误：{e}")
        sys.exit(1)

    # --- 执行抓取 ---
    output_root = os.path.join(output_dir, username)

    # 确定本次需产出的格式列表
    if fmt == "both":
        active_formats = ["md", "html"]
        fmt_label = "MD+HTML"
    else:
        active_formats = [fmt]
        fmt_label = fmt.upper()

    # 为所有参与格式创建目录，记录配置
    format_config = {}
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
        format_config[f] = cfg

    # --- 加载进度文件 ---
    progress_file = os.path.join(output_root, ".progress.json")

    if no_incremental:
        last_time = 0
        prev_answers = 0
        prev_articles = 0
        print(f"\n[全量模式] 已禁用增量，将重新抓取所有点赞内容（{fmt_label}）...")
    else:
        # both 模式取两个格式中最早的时间戳，确保不漏内容
        min_last = None
        for f in active_formats:
            p = load_progress(progress_file, f)
            pt = p.get("last_activity_time", 0)
            if min_last is None or pt < min_last:
                min_last = pt
        last_time = min_last or 0
        # 以下仅用于日志展示，取第一个格式的计数作为参考
        ref_p = load_progress(progress_file, active_formats[0])
        prev_answers = ref_p.get("answer_count", 0)
        prev_articles = ref_p.get("article_count", 0)

        if last_time > 0:
            last_time_str = datetime.fromtimestamp(last_time).strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[增量模式] 上次已抓取至 {last_time_str}（{prev_answers} 个回答 + {prev_articles} 篇文章）")
            print(f"将只抓取此时间之后的新点赞（{fmt_label}）...")
        else:
            print(f"\n[全量模式] 首次运行，将抓取所有点赞内容（{fmt_label}）...")

    answer_count = 0
    article_count = 0
    total_items = 0
    new_latest_time = last_time
    url = build_activity_url(username)
    page_num = 0
    is_end = False
    stopped_early = False

    print(f"\n开始抓取用户 [{username}] 的点赞动态...\n")

    while not is_end:
        page_num += 1
        print(f"正在获取第 {page_num} 页...", end=" ", flush=True)

        # 带重试的请求
        response = None
        for retry in range(MAX_RETRIES):
            try:
                response = requests.get(url, headers=base_headers, timeout=30)
                if response.status_code == 200:
                    break
                if response.status_code == 429:
                    print(f"(被限流，等待 {RETRY_DELAY}s)", end=" ")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"(HTTP {response.status_code})", end=" ")
                    time.sleep(RETRY_DELAY)
            except requests.RequestException as e:
                print(f"(网络错误: {e})", end=" ")
                time.sleep(RETRY_DELAY)
        else:
            print("\n请求失败次数过多，中止。请检查 Cookie 是否有效。")
            break

        try:
            data = response.json()
        except Exception:
            print("JSON 解析失败，跳过本页。")
            time.sleep(PAGE_DELAY)
            continue

        if "error" in data:
            print(f"\nAPI 返回错误：{data.get('error', {}).get('message', '未知错误')}")
            print("请检查 Cookie 是否有效或是否被风控。")
            break

        is_end = data.get("paging", {}).get("is_end", True)
        url = data.get("paging", {}).get("next", "")
        items = data.get("data", [])

        page_new_count = 0
        for item in items:
            action = item.get("action_text", "")
            if action not in ("赞同了回答", "赞同了文章"):
                continue

            activity_time = item.get("created_time", 0)

            if activity_time > new_latest_time:
                new_latest_time = activity_time

            # 增量模式：遇到 <= 上次记录时间的内容，停止抓取
            if last_time > 0 and activity_time <= last_time:
                stopped_early = True
                break

            try:
                # 提取公共字段
                target = item.get("target", {})
                time_upvoted = datetime.fromtimestamp(activity_time)
                date_str = time_upvoted.strftime("%Y-%m-%d")

                if action == "赞同了回答":
                    question = target.get("question", {})
                    title = sanitize_filename(question.get("title", "无标题"))
                    answer_count += 1
                else:  # 赞同了文章
                    title = sanitize_filename(target.get("title", "无标题"))
                    article_count += 1

                # 本次爬取至少写出一个格式才算新增
                wrote_any = False
                for f in active_formats:
                    cfg = format_config[f]
                    if action == "赞同了回答":
                        file_dir = cfg["answers_dir"]
                    else:
                        file_dir = cfg["articles_dir"]
                    filename = f"{date_str}_{title}{cfg['ext']}"
                    filepath = os.path.join(file_dir, filename)

                    if os.path.exists(filepath):
                        continue

                    if action == "赞同了回答":
                        content = format_html_answer(item) if f == "html" else format_md_answer(item)
                    else:
                        content = format_html_article(item) if f == "html" else format_md_article(item)

                    with open(filepath, "w", encoding="utf-8") as fh:
                        fh.write(content)
                    wrote_any = True

                if wrote_any:
                    page_new_count += 1
                    total_items += 1

                # 达到 limit 上限
                if limit and total_items >= limit:
                    stopped_early = True
                    break
            except Exception as e:
                print(f"\n处理单条数据时出错：{e}，跳过。")
                continue

        if stopped_early:
            if limit and total_items >= limit:
                print(f"本页新增 {page_new_count} 条，已达到上限 ({limit} 条)，停止。")
            else:
                print(f"本页新增 {page_new_count} 条，已到达上次记录时间，停止翻页。")
            break

        print(f"本页新增 {page_new_count} 条（累计：{answer_count} 个回答 + {article_count} 篇文章）")

        if page_new_count == 0 and not is_end:
            print("本页无点赞动态，继续翻页...")

        time.sleep(PAGE_DELAY)

    # 保存进度（每个格式独立）
    for f in active_formats:
        # 对于 both 模式，每个格式的计数是独立的，这里用本次新增的计数加上之前该格式的计数
        p = load_progress(progress_file, f)
        prev_f_answers = p.get("answer_count", 0)
        prev_f_articles = p.get("article_count", 0)
        total_f_answers = prev_f_answers + answer_count
        total_f_articles = prev_f_articles + article_count

        # 如果被 limit 截断，last_activity_time 保持原值，不推进
        if limit and total_items >= limit:
            save_progress(progress_file, f, last_time, total_f_answers, total_f_articles)
            if f == active_formats[0]:
                print(f"[注意] 因 --limit {limit} 截断，进度时间戳未推进，下次运行将继续从 {datetime.fromtimestamp(last_time).strftime('%Y-%m-%d %H:%M:%S')} 开始")
        else:
            save_progress(progress_file, f, new_latest_time, total_f_answers, total_f_articles)

    print(f"\n========== 导出完成 ==========")
    print(f"本次新增：{answer_count} 个回答 + {article_count} 篇文章")
    print(f"输出格式：{fmt_label}")

    # 列出所有生成的目录
    for f in active_formats:
        cfg = format_config[f]
        print(f"  {f.upper()} 回答目录：{cfg['answers_dir']}")
        print(f"  {f.upper()} 文章目录：{cfg['articles_dir']}")

    return answer_count, article_count


# ============================================================
# CLI 入口
# ============================================================

def main():
    """CLI入口函数"""
    parser = argparse.ArgumentParser(
        description="导出知乎用户的所有点赞回答和文章为本地 Markdown 文件（支持增量爬取、Cookie/扫码双模式登录）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --cookie "your_cookie"
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -o D:\\MyExports --limit 20
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx --no-incremental --visible
  python zhihu_upvote_exporter.py https://www.zhihu.com/people/xxx -f html
        """,
    )

    # ---- 输入选项 ----
    input_group = parser.add_argument_group("输入选项")
    input_group.add_argument(
        "user_url",
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
        help="输出格式：md=Markdown 纯文本，html=HTML 富文本（保留原始排版、图片、样式），both=同时导出两种（默认: md）",
    )
    output_group.add_argument(
        "-l", "--limit",
        type=int,
        default=None,
        help="限制抓取条数，达到上限后停止（默认: 不限制）",
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

    # ---- 浏览器选项 ----
    browser_group = parser.add_argument_group("浏览器选项（Playwright 扫码时生效）")
    browser_group.add_argument(
        "--visible",
        action="store_true",
        help="扫码登录时显示浏览器窗口（默认: 后台运行）",
    )

    args = parser.parse_args()

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
        )
    except KeyboardInterrupt:
        print("\n\n操作被用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n处理失败: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
