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
    r"\$[LDdbe]|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}|^[0-9a-f]{16,}$"
)


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
    extra_text = re.sub(r"\s+", " ", " ".join(extra))
    return f"TITLE: {title}\n{text}\nDATA: {extra_text}"


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
