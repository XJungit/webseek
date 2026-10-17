#!/usr/bin/env python3
"""webseek - 实时监控网页变化.

用法:
  python monitor.py --once       # 抓取一轮并退出
  python monitor.py              # 持续轮询监控 (Ctrl+C 退出)
  python monitor.py --config x.yaml
"""
import argparse
import difflib
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime

import yaml

import crawler
import notify
from storage import Storage

log = logging.getLogger("webseek")


def setup_logging():
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter(fmt))
    root.addHandler(h)


def check_page(url, title, cfg, storage):
    """抓取并检测变化. 变化时返回 (title, page_title, url, diff_lines, snapshot), 否则 None."""
    try:
        resp = crawler.fetch_html(url, cfg)
    except crawler.FetchError as e:
        log.error("抓取失败: %s - %s", title, e)
        return None

    content = crawler.extract_content(resp.text, cfg)

    # 失效页检测: 非首页URL却抓到首页内容 -> 页面已删除/重定向, 跳过diff只记失效
    if crawler.is_homepage_fallback(content, url):
        changed = storage.record_fallback(url, resp.url)
        storage.save()
        if changed:
            log.warning("页面可能已失效/重定向到首页(已跳过diff): %s -> %s",
                        title, resp.url)
        return None

    digest = crawler.content_hash(crawler.stable_part(content))
    old_content = storage.old_content(url)

    # 兼容老基线: 老 state.json 里 content 是"旧版抓取格式"(DATA 含噪声),
    # 直接用稳定部分比对即可判断真变化, 无需整库重置; 若稳定部分已被更新
    # (存储的是新格式), 则直接退化为 digest 比对.
    if old_content is not None:
        old_stable = crawler.stable_part(old_content)
        new_stable = crawler.stable_part(content)
        if _LEGACY_STABLE_RE.search(old_stable):
            if old_stable == new_stable:
                log.debug("无变化: %s", title)
                return None
        else:
            changed = storage.record_ok(url, digest, content)
            if not changed:
                log.debug("无变化: %s", title)
                return None
            diff_lines = [l for l in difflib.unified_diff(
                old_stable.splitlines(), new_stable.splitlines(),
                fromfile="旧", tofile="新", lineterm="", n=2)
                if l.startswith(("+", "-", "@"))]
            if not diff_lines:
                log.info("仅 DATA 段漂移, 误报过滤: %s", title)
                return None
            storage.save()
            return _finish_change(cfg, storage, url, title, content,
                                  diff_lines)
    else:
        storage.record_ok(url, digest, content)
        storage.save()
        log.info("首次记录: %s (%d 字符)", title, len(content))
        return None

    # --- 老基线分支: 稳定部分已变化, 落盘新基线后输出 ---
    # (理论上此时 diff_lines 必非空, 兜底防空 diff 污染报告/邮件)
    storage.record_ok(url, digest, content)
    storage.save()

    diff_lines = [l for l in difflib.unified_diff(
        old_stable.splitlines(), new_stable.splitlines(),
        fromfile="旧", tofile="新", lineterm="", n=2)
        if l.startswith(("+", "-", "@"))]
    if not diff_lines:
        log.info("仅 DATA 段漂移, 误报过滤: %s", title)
        return None
    return _finish_change(cfg, storage, url, title, content, diff_lines)


def _basic_mail(title, page_title, url, diff_lines, max_lines=30, width=160):
    """build_mail 异常时的兜底摘要: 朴素纯文本, 保证通知不丢."""
    out = [f"标题: {title}"]
    if page_title:
        out.append(f"页面: {page_title}")
    out.append(f"链接: {url}")
    out.append(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    rows = [l for l in diff_lines if l and not l.startswith("@@")]
    out.append("")
    out.append(f"共 {len(rows)} 行差异（邮件排版已降级）:")
    for line in rows[:max_lines]:
        line = line.rstrip()
        out.append(line if len(line) <= width else line[:width] + "…")
    if len(rows) > max_lines:
        out.append(f"…（其余 {len(rows) - max_lines} 行见 changes.log）")
    return "\n".join(out)


def _finish_change(cfg, storage, url, title, content, diff_lines):
    """变化已确认: 打快照、记日志、发通知, 返回报告元组."""
    page_title = _page_title(content)
    snapshot = storage.save_snapshot(url, content, True)
    log.info("===== 检测到变化: %s =====", title)
    log.info("页面: %s", page_title)
    log.info("URL: %s", url)
    log.info("快照已保存: %s", snapshot)
    for line in diff_lines:
        log.info("%s", line)
    notify.send_webhook(cfg.get("notify", {}), f"网页变化: {title}", url)
    try:
        mail_text, mail_html = build_mail(title, page_title, url, diff_lines)
    except Exception:
        # 邮件排版只是展示层: 此时基线已落盘, 若让它把整轮打挂,
        # 这次变化就永远不会再被检测到 —— 宁可降级成朴素摘要.
        log.exception("邮件正文生成失败, 降级为精简摘要")
        mail_text, mail_html = _basic_mail(title, page_title, url, diff_lines), None
    notify.send_email(cfg.get("notify", {}), f"网页变化: {title}",
                      mail_text, mail_html)
    write_changelog(cfg, url, title, page_title, diff_lines, snapshot)
    return (title, page_title, url, diff_lines, snapshot)


# 老抓取格式的指纹: 可见正文里混入了 RSC 内层转义串/模板占位
# (如 \" \\n 、heroJoinTitle1、token 72), 新版 extract_content 剥离了它们.
_LEGACY_STABLE_RE = re.compile(
    r'\\" ?\\n|heroJoinTitle\d|heroCta\d|platformRotating\d|platformFeature\d|'
    r"token \d|notFoundLine\d|newsPageTitle\d|transparencyFooterCta\d|"
    r"NextPageVisitSetup|NextPageVisitRecorder|RememberLocale"
)


# 页面正文经 extract_content 归一化后常是数千字的超长单行, 若按行从头截断, 被剪掉的
# 往往正是变化处 —— 邮件里两行看起来一模一样, 完全看不出改了什么. 因此邮件改为
# "字符级变化点 + 就近上下文" 展示; 完整 diff 仍保留在 GitHub Issue / changes.log.
MAIL_MAX_FRAGS = 15      # 邮件里最多展示多少处变化
INLINE_CTX = 40          # 每处变化前后保留的上下文字符数
INLINE_MERGE = 10        # 相邻变化点间距小于该值时合并为同一处
INLINE_MAX_FRAGS = 4     # 单个 -(旧)/+(新) 行对内最多展示的变化点
INLINE_DIFF_MAX = 3000   # 行内差异区的字符上限, 超过则整段作为一处变化(防卡顿)
SEG_MAX = 120            # 单个变化片段最多展示的字符数


def _prefix_tail(text, width=INLINE_CTX):
    """变化点之前的上下文: 保留紧邻的尾部字符, 左侧省略."""
    text = (text or "").strip()
    return text if len(text) <= width else "…" + text[-width:]


def _suffix_head(text, width=INLINE_CTX):
    """变化点之后的上下文: 保留紧邻的头部字符, 右侧省略."""
    text = (text or "").strip()
    return text if len(text) <= width else text[:width] + "…"


def _clip_seg(seg):
    """单个变化片段过长时截断, 并标注原始长度."""
    seg = seg or ""
    if len(seg) <= SEG_MAX:
        return seg
    return f"{seg[:SEG_MAX]}…(共 {len(seg)} 字)"


def _common_edges(old, new):
    """公共前缀长度与公共后缀长度(两者不重叠), 用于快速定位差异区."""
    n = min(len(old), len(new))
    pre = 0
    while pre < n and old[pre] == new[pre]:
        pre += 1
    suf = 0
    while (suf < n - pre
           and old[len(old) - 1 - suf] == new[len(new) - 1 - suf]):
        suf += 1
    return pre, suf


def _char_spans(old, new, base_i, base_j):
    """在 old/new 上做字符级比对, 返回整行坐标下的差异区间列表."""
    return [(base_i + i1, base_i + i2, base_j + j1, base_j + j2)
            for tag, i1, i2, j1, j2
            in difflib.SequenceMatcher(None, old, new).get_opcodes()
            if tag != "equal"]


# 句子边界: 中文句末标点直接切; 英文句点仅在其后是空白时切(避免切碎 URL/缩写)
_SENT_END = re.compile(r"[。！？；!?;]+|\.(?=\s)")


def _split_sentences(text):
    """按句末标点切段, 返回 (段列表, 每段起始偏移)."""
    parts, offs, pos = [], [], 0
    for m in _SENT_END.finditer(text):
        parts.append(text[pos:m.end()])
        offs.append(pos)
        pos = m.end()
    if pos < len(text):
        parts.append(text[pos:])
        offs.append(pos)
    return parts, offs


def _sentence_spans(old, new, base):
    """差异区过大时按句子对齐(序列长度小, 很快), 组内能精修就再精修一次.

    整页被改写时逐字比对既慢又不可读; 按句子对齐后, "改了哪几句"能逐处列出,
    组内体量不大时再落到具体字词.
    """
    o_parts, o_offs = _split_sentences(old)
    n_parts, n_offs = _split_sentences(new)
    spans = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, o_parts, n_parts).get_opcodes():
        if tag == "equal":
            continue
        # opcode 的起点/终点都可能落在段序列末尾之外(整段追加或删除),
        # 一律按文本长度兜底, 否则列表越界会把整轮监控打挂.
        i1c = base + (o_offs[i1] if i1 < len(o_offs) else len(old))
        i2c = base + (o_offs[i2] if i2 < len(o_offs) else len(old))
        j1c = base + (n_offs[j1] if j1 < len(n_offs) else len(new))
        j2c = base + (n_offs[j2] if j2 < len(n_offs) else len(new))
        if (i2c - i1c) + (j2c - j1c) <= INLINE_DIFF_MAX:
            spans.extend(_char_spans(old[i1c - base:i2c - base],
                                     new[j1c - base:j2c - base], i1c, j1c))
        else:
            spans.append((i1c, i2c, j1c, j2c))
    return spans


def _frags_for_pair(old, new):
    """对一对 -(旧)/+(新) 行做行内 diff, 返回变化点列表.

    先剥掉公共前后缀把差异区缩到最小(超长正文也能秒级完成); 差异区不大时直接
    字符级比对, 精确定位"改了哪几个字"; 差异区很大(整段改写)则退到句子级对齐,
    逐处列出改动, 而不是把整行从头截断了事.
    """
    if (not old and not new) or old == new:
        return []
    pre, suf = _common_edges(old, new)
    old_mid = old[pre:len(old) - suf]
    new_mid = new[pre:len(new) - suf]

    if len(old_mid) + len(new_mid) <= INLINE_DIFF_MAX:
        spans = _char_spans(old_mid, new_mid, pre, pre)
    else:
        spans = _sentence_spans(old_mid, new_mid, pre)

    merged = []
    for i1, i2, j1, j2 in spans:
        if (merged and i1 - merged[-1][1] <= INLINE_MERGE
                and j1 - merged[-1][3] <= INLINE_MERGE):
            merged[-1] = (merged[-1][0], i2, merged[-1][2], j2)
        else:
            merged.append((i1, i2, j1, j2))

    frags = []
    for i1, i2, j1, j2 in merged:
        suffix_src = old[i2:] if i2 < len(old) else new[j2:]
        frags.append({
            "old": old[i1:i2],
            "new": new[j1:j2],
            "prefix": _prefix_tail(old[max(0, i1 - INLINE_CTX):i1]),
            "suffix": _suffix_head(suffix_src),
        })
    return frags


def _diff_fragments(diff_lines):
    """把 unified diff 解析成字符级变化点, 返回 (变化点列表, 被折叠的数量).

    丢弃 ---/+++ 文件头与 @@ 行(它们不是内容差异, 之前在邮件里被当成
    删除/新增行显示); 连续的 -(旧)/+(新) 行按顺序配对后做行内比对.
    """
    frags, extra = [], 0
    dels, adds = [], []

    def flush():
        nonlocal dels, adds, extra
        for i in range(max(len(dels), len(adds))):
            old = dels[i][1:] if i < len(dels) else ""
            new = adds[i][1:] if i < len(adds) else ""
            pair = _frags_for_pair(old, new)
            frags.extend(pair[:INLINE_MAX_FRAGS])
            extra += max(0, len(pair) - INLINE_MAX_FRAGS)
        dels, adds = [], []

    for line in diff_lines:
        if (not line or line.startswith("@@")
                or line.startswith("--- ") or line.startswith("+++ ")):
            continue
        if line.startswith("-"):
            if adds:
                flush()
            dels.append(line)
        elif line.startswith("+"):
            adds.append(line)
        else:
            flush()
    flush()
    return frags, extra


# 拼接上下文与变化标记时, 紧邻标点则不再补空格, 读起来更接近原文
_NO_SPACE_AFTER = "。，、；：！？）》」』】…,.;:!?)]}"
_NO_SPACE_BEFORE = "（《「『【([{"


def _join_ctx(parts):
    out = ""
    for part in parts:
        if not part:
            continue
        if out and out[-1] not in _NO_SPACE_AFTER and part[0] not in _NO_SPACE_BEFORE:
            out += " "
        out += part
    return out


def _frag_text(frag):
    """纯文本版的一处变化: 上下文 + 「旧」→「新」."""
    old, new = _clip_seg(frag["old"]), _clip_seg(frag["new"])
    if old and new:
        mid = f"「{old}」→「{new}」"
    elif old:
        mid = f"删除「{old}」"
    else:
        mid = f"新增「{new}」"
    return _join_ctx([frag["prefix"], mid, frag["suffix"]])


def _frag_html(esc, frag):
    """HTML 版的一处变化; 全部用行内样式, 避免邮件客户端丢弃 <style>."""
    ctx = "color:#57606a"
    del_box = ("background:#ffeef0;color:#82071e;padding:1px 3px;"
               "border-radius:3px;margin:0 2px")
    add_box = ("background:#e6ffed;color:#0a3622;padding:1px 3px;"
               "border-radius:3px;margin:0 2px")
    old, new = _clip_seg(frag["old"]), _clip_seg(frag["new"])
    if old and new:
        body = (f'<span style="{del_box}">{esc(old)}</span>'
                '<span style="color:#57606a;margin:0 4px">→</span>'
                f'<span style="{add_box}">{esc(new)}</span>')
    elif old:
        body = (f'<span style="{ctx};margin-right:4px">删除</span>'
                f'<span style="{del_box}">{esc(old)}</span>')
    else:
        body = (f'<span style="{ctx};margin-right:4px">新增</span>'
                f'<span style="{add_box}">{esc(new)}</span>')
    return ('<div style="margin:6px 0;padding:6px 8px;background:#f6f8fa;'
            'border-radius:6px;white-space:pre-wrap;word-break:break-word;'
            'line-height:1.7">'
            f'<span style="{ctx}">{esc(frag["prefix"])}</span>{body}'
            f'<span style="{ctx}">{esc(frag["suffix"])}</span></div>')


def build_mail(title, page_title, url, diff_lines):
    """组装邮件正文, 返回 (纯文本版, HTML 版).

    只展示"改了什么": 对每对 -(旧)/+(新) 行做字符级 diff, 保留变化片段与就近
    上下文, 因此超长正文也能一眼看出改动; 变化点数量与单点长度均封顶, 避免
    内容多时糊成一片. 完整 diff 仍保留在 GitHub Issue / changes.log.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    frags, folded = _diff_fragments(diff_lines)
    shown = frags[:MAIL_MAX_FRAGS]
    omitted = len(frags) - len(shown) + folded

    head = [f"标题: {title}"]
    if page_title:
        head.append(f"页面: {page_title}")
    head.append(f"链接: {url}")
    head.append(f"时间: {ts}")

    text = list(head) + [""]
    if not frags:
        text.append("（无实质内容差异）")
    else:
        text.append(f"共 {len(frags) + folded} 处变化（「旧」→「新」为改动处）:")
        for i, f in enumerate(shown, 1):
            text.append(f"  {i}. {_frag_text(f)}")
        if omitted:
            text.append(f"  …（已省略其余 {omitted} 处）")
        text.append("完整 diff 见 GitHub Issue / changes.log")

    import html as _html
    esc = _html.escape
    rows = [_frag_html(esc, f) for f in shown]
    if omitted:
        rows.append('<div style="color:#57606a;margin-top:6px">… 已省略其余 '
                    f"{omitted} 处，完整 diff 见 GitHub Issue / changes.log</div>")
    if not rows:
        rows.append('<div style="color:#57606a">（无实质内容差异）</div>')
    detail_head = ("<p><b>变化详情:</b>"
                   + (f"共 {len(frags) + folded} 处"
                      "（「旧」→「新」为改动处）</p>" if frags else "</p>"))
    html_body = (
        '<html><body style="font-family:-apple-system,Segoe UI,Roboto,'
        'PingFang SC,Microsoft YaHei,sans-serif;font-size:14px;color:#24292f">'
        f"<p><b>标题:</b> {esc(title)}<br>"
        + (f"<b>页面:</b> {esc(page_title)}<br>" if page_title else "")
        + f'<b>链接:</b> <a href="{esc(url)}">{esc(url)}</a><br>'
        + f"<b>时间:</b> {esc(ts)}</p>"
        + detail_head
        + "".join(rows)
        + '<div style="color:#57606a;font-size:12px;margin-top:8px">'
          "完整 diff 见 GitHub Issue / changes.log</div>"
        + "</body></html>"
    )
    return "\n".join(text), html_body


def _page_title(content):
    """从提取内容解析页面真实标题 (TITLE: xxx)."""
    line = content.splitlines()[0] if content else ""
    return line[len("TITLE: "):].strip() if line.startswith("TITLE: ") else ""


def write_changelog(cfg, url, title, page_title, diff_lines, snapshot):
    """追加写入变化历史 changes.log, 方便事后查看."""
    path = cfg.get("changelog_file", "changes.log")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"## {ts} | {title}\n")
        if page_title:
            f.write(f"页面: {page_title}\n")
        f.write(f"URL: {url}\n")
        if snapshot:
            f.write(f"快照: {snapshot}\n")
        f.write("```diff\n")
        f.write("\n".join(diff_lines) + "\n")
        f.write("```\n\n")


def run_once(cfg, storage, report_file=None):
    """跑完所有目标, 返回处理异常的页面数.

    单个页面出意外只记日志并跳过, 不中断整轮: 否则后面的目标全都不看,
    本轮已保存的基线还会让出问题的这次变化再也通知不到.
    """
    changes = 0
    seen = 0
    failed = 0
    reports = []
    for target in cfg.get("targets", []):
        url = target["url"]
        title = target.get("title", url)
        if target.get("sitemap"):
            try:
                pages = crawler.fetch_sitemap_urls(
                    url, cfg, target.get("sitemap_exclude", []))
            except crawler.FetchError as e:
                log.error("sitemap 抓取失败: %s - %s", title, e)
                continue
            seen += len(pages)
            log.info("从 sitemap 发现 %d 个页面: %s", len(pages), title)
            for page in sorted(pages):
                try:
                    r = check_page(page, f"{title} :: {page}", cfg, storage)
                except Exception:
                    failed += 1
                    log.exception("页面处理异常, 已跳过: %s", page)
                    continue
                if r:
                    changes += 1
                    reports.append(r)
        else:
            seen += 1
            try:
                r = check_page(url, title, cfg, storage)
            except Exception:
                failed += 1
                log.exception("目标处理异常, 已跳过: %s", title)
                continue
            if r:
                changes += 1
                reports.append(r)
    if changes:
        log.info("本轮完成: 共 %d 处变化 (监控 %d 个页面, 详见 changes.log)", changes, seen)
        if report_file:
            write_report(report_file, reports)
    else:
        log.info("本轮完成: 无变化 (监控 %d 个页面)", seen)
    if failed:
        log.error("本轮有 %d 个页面处理异常, 详见上方日志", failed)
    return failed


REPORT_WRAP = 100        # 普通文本行的折行宽度(手机端会自然换行, 可以留宽些)
REPORT_CODE_COLS = 64    # 代码块的折行宽度, 按"显示列"算: 等宽字体下 CJK 占 2 列
REPORT_CODE_MAX_LINES = 400   # 折叠块最多保留的行数, 超出截断(全文见仓库 changes.log)


def _disp_width(ch):
    """字符在等宽字体下占的列数(东亚宽字符占 2 列)."""
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _wrap_cols(line, cols=REPORT_CODE_COLS):
    """按显示列宽折行.

    只按字符数折是不够的: 一行 50 个汉字在等宽字体下相当于 100 列, 放进
    代码块照样比手机屏宽一倍. 这里按实际列宽折, 保证渲染出来不会横向溢出.
    """
    line = line.rstrip()
    if not line:
        return [""]
    out, cur, width = [], [], 0
    for ch in line:
        w = _disp_width(ch)
        if width + w > cols and cur:
            out.append("".join(cur))
            cur, width = [], 0
        cur.append(ch)
        width += w
    out.append("".join(cur))
    return out


def _wrap_long(line, width=REPORT_WRAP):
    """把超长行硬折成多行.

    正文经归一化后常是上万字的单行, 直接写进 Issue 会被 GitHub 原样塞进
    通知邮件: 邮件宽度被撑到屏幕的好几倍, 客户端只能整页缩小, 手机上就是
    "一片空白 + 字小到看不见". 折行后任何一行都不会超过 width.
    """
    line = line.rstrip()
    if len(line) <= width:
        return [line]
    return [line[i:i + width] for i in range(0, len(line), width)]


def write_report(path, reports):
    """写入 GitHub Actions 通知报告 (markdown). 云端不存快照, 故不含快照链接.

    这份报告会作为 GitHub Issue 正文, 并由 GitHub 直接发进通知邮件, 所以
    排版按"手机可读"来做: 先给变化点+就近上下文(与邮件一致), 完整 diff 折行
    后收进折叠块.
    """
    with open(path, "w", encoding="utf-8") as f:
        for title, page_title, url, diff_lines, _snapshot in reports:
            f.write(f"## {title}\n\n")
            if page_title:
                f.write(f"页面: {page_title}\n\n")
            f.write(f"URL: {url}\n\n")
            frags, folded = _diff_fragments(diff_lines)
            if frags:
                shown = frags[:MAIL_MAX_FRAGS]
                f.write(f"共 {len(frags) + folded} 处变化"
                        "（「旧」→「新」为改动处）:\n\n")
                for i, frag in enumerate(shown, 1):
                    for line in _wrap_long(f"{i}. {_frag_text(frag)}"):
                        f.write(line + "\n")
                    f.write("\n")
                omitted = len(frags) + folded - len(shown)
                if omitted:
                    f.write(f"…（已省略其余 {omitted} 处）\n\n")
            else:
                f.write("（无实质内容差异）\n\n")
            block = []
            for line in diff_lines:
                block.extend(_wrap_cols(line))
            truncated = len(block) > REPORT_CODE_MAX_LINES
            if truncated:
                block = block[:REPORT_CODE_MAX_LINES]
            note = "（超长已截断, 全文见仓库 changes.log）" if truncated else "（已折行）"
            f.write(f"<details><summary>完整 diff{note}</summary>\n\n"
                    "```diff\n")
            f.write("\n".join(block) + "\n")
            f.write("```\n\n</details>\n\n")


def main():
    ap = argparse.ArgumentParser(description="webseek 网页变化监控")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--once", action="store_true", help="只运行一轮后退出")
    ap.add_argument("--report-file", help="变化时写入 markdown 通知报告(GitHub Actions 用)")
    args = ap.parse_args()

    setup_logging()
    reconfig = getattr(sys.stdout, "reconfigure", None)
    if reconfig:
        try:
            reconfig(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    storage = Storage(cfg.get("state_file", "state.json"),
                      cfg.get("snapshot_dir", "snapshots"))

    if args.once:
        failed = run_once(cfg, storage, report_file=args.report_file)
        if failed:
            # 仍以非 0 退出以便 Actions 发出失败通知; 其余页面已正常处理,
            # 状态由后续 if: always() 的步骤照常提交.
            raise SystemExit(1)
        return

    interval = cfg.get("interval_seconds", 600)
    log.info("webseek 启动, 每 %d 秒轮询一次 (Ctrl+C 退出)", interval)
    while True:
        try:
            run_once(cfg, storage)
        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("本轮执行异常")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
