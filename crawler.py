"""网页抓取与正文规范化提取."""
import hashlib
import json
import re
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class FetchError(Exception):
    pass


def _get_headers(cfg):
    h = dict(HEADERS)
    h["User-Agent"] = cfg.get("user_agent", "webseek/1.0")
    return h


def fetch_html(url, cfg):
    """抓取 URL, 返回响应文本. 失败时按配置重试."""
    retries = max(1, cfg.get("retries", 3))
    timeout = cfg.get("timeout_seconds", 30)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                url, headers=_get_headers(cfg), timeout=timeout,
                allow_redirects=True,
            )
            r.raise_for_status()
            if r.encoding is None or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or "utf-8"
            if not r.text:
                raise FetchError("empty body")
            return r
        except (requests.RequestException, FetchError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(5 * attempt)
    raise FetchError(f"{url} failed after {retries} tries: {last_err}")


def _extract_json_strings(text):
    """从 __NEXT_DATA__ 等内联 JSON 提取全部字符串值 (SSR 页面数据)."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ""
    parts = []
    stack = [obj]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            if len(v) >= 6:
                parts.append(v)
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return " ".join(parts)


_NOISE = re.compile(
    r"static/|\.js\b|\.css\b|\.png\b|\.jpg\b|\.svg\b|\.json\b|%5B|%5D|"
    r"\$[LDdbe]|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}|^[0-9a-f]{16,}$|"
    # 以下为易变 SSR/RSC 噪声: 随机会话 ID / 埋点令牌 / 模板类, 每次请求都不同
    r"[A-Za-z0-9_-]{14,}"         # 随机 ID/哈希 (ruQKed94s2fsWmYzhepsH, gaW0_wU5RdVWkTUwK558U,
                                  # Qn_4kyupWIM-qRJH5Zwpn; 含 -/_ 分隔的长令牌也一起丢)
    r"|font/woff2|width=device-width|This page could not be found|"
    r"next-error|__className|ToastSetup|DataFinderBase|ApmReport|next_f\.push|"
    r"\[|\]|%r"                    # RSC 分块标记 (I[67595,[) 与 %r 等模板占位
)


def _scrub_extra(text):
    """剔除 SSR 数据中的易变噪声 token, 避免埋点/会话令牌导致误报.

    __NEXT_DATA__ / next_f.push 提取出的字符串未经 _NOISE 过滤, 其中常含每次
    请求都变的随机会话 ID (如 ruQKed94s2fsWmYzhepsH) 与 RSC 分块标记, 直接进
    哈希会让几乎每个页面每轮都"变化". 这里按空白切 token, 命中 _NOISE 的整体丢弃.
    """
    if not text:
        return ""
    out = []
    for tok in re.split(r"\s+", text):
        if not _NOISE.search(tok):
            out.append(tok)
    return " ".join(out)


def _keep_string(inner):
    """判断 RSC 内层字符串是否值得保留: 含中文/数字/大写开头的标题式文本."""
    if not inner or len(inner) < 6:
        return False
    if re.search(r"[\u4e00-\u9fff]", inner):
        return True
    if re.search(r"\d", inner):
        return True
    if (re.fullmatch(r"[A-Z][A-Za-z0-9 ._\-*:/#&()%+,°]*", inner)
            and inner.count(" ") <= 12):
        return True
    return False


def _extract_next_f_strings(body):
    """从 Next.js __next_f.push 流式数据提取可读字符串, 过滤路径/时间戳噪声."""
    parts = []
    seen = set()
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', body):
        try:
            s = m.group(1).encode("latin-1", "ignore").decode("unicode_escape")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        for inner in re.findall(r'"([^"]*)"', s):
            inner = inner.replace("\\n", " ").replace("\n", " ").strip()
            if (inner not in seen and not _NOISE.search(inner)
                    and not any(c in inner for c in ("{", ";"))
                    and _keep_string(inner)):
                seen.add(inner)
                parts.append(inner)
    return " ".join(parts)


def extract_content(html, cfg):
    """从 HTML 提取规范化正文: 标题 + 正文纯文本, 用于哈希与 diff."""
    soup = BeautifulSoup(html, "html.parser")
    extra = []
    for s in soup.find_all("script"):
        sid = str(s.get("id") or "").lower()
        body = s.string or ""
        if sid == "__next_data__":
            extra.append(_extract_json_strings(body))
        elif "next_f.push" in body:
            extra.append(_extract_next_f_strings(body))
        s.decompose()
    for tag in soup(["style", "noscript", "template"]):
        tag.decompose()

    if cfg.get("ignore_blocks", True):
        for sel in ["nav", "footer", "header", "aside", ".navbar", "#footer",
                    ".pagination", "form", "svg", "iframe"]:
            for node in soup.select(sel):
                node.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""
    main = soup.select_one("main") or soup.select_one("article") or soup.body or soup
    text = main.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    extra_text = _scrub_extra(re.sub(r"\s+", " ", " ".join(extra)))
    return f"TITLE: {title}\n{text}\nDATA: {extra_text}"


def stable_part(content):
    """变化检测用的稳定部分: 标题 + 可见正文, 排除易变的 SSR DATA 段.

    DATA 段(埋点/分片 id/翻译 key 等)每轮都可能杂散变化且无业务价值
    (如官网主页的 Qn_4kyupWIM-qRJH5Zwpn), 不再参与哈希与 diff;
    仍会存入快照/changes.log 供追查.
    """
    if not content:
        return ""
    idx = content.find("\nDATA: ")
    return content[:idx] if idx != -1 else content


def fetch_sitemap_urls(sitemap_url, cfg, exclude=None):
    """从 sitemap.xml 发现全部页面 URL. exclude 为需过滤的子串列表."""
    r = fetch_html(sitemap_url, cfg)
    soup = BeautifulSoup(r.text, "xml")
    urls = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
    exclude = exclude or []
    return [u for u in urls if u and not any(
        s in u.lower() for s in exclude)]


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# 首页特征标题: 非首页URL却抓到这些 -> 页面已失效/回退到首页
HOMEPAGE_TITLES = ("Your First API Call", "首次调用 API", "DeepSeek | 深度求索")
_HOMEPAGE_URLS = (
    "https://api-docs.deepseek.com",
    "https://api-docs.deepseek.com/zh-cn",
    "https://www.deepseek.com",
)


def is_homepage_fallback(content, url):
    """非首页URL却抓到首页内容 -> 页面失效/重定向到首页(应跳过diff)."""
    if (url or "").rstrip("/") in _HOMEPAGE_URLS:
        return False
    first = content.splitlines()[0] if content else ""
    return any(t in first for t in HOMEPAGE_TITLES)
