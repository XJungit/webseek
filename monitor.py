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
import sys
import time
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

    digest = crawler.content_hash(content)
    old_content = storage.old_content(url)

    if old_content is None:
        storage.record_ok(url, digest, content)
        storage.save()
        log.info("首次记录: %s (%d 字符)", title, len(content))
        return None

    changed = storage.record_ok(url, digest, content)
    if not changed:
        log.debug("无变化: %s", title)
        return None

    storage.save()

    diff_lines = [l for l in difflib.unified_diff(
        old_content.splitlines(), content.splitlines(),
        fromfile="旧", tofile="新", lineterm="", n=2)
        if l.startswith(("+", "-", "@"))]

    page_title = _page_title(content)

    snapshot = storage.save_snapshot(url, content, True)
    log.info("===== 检测到变化: %s =====", title)
    log.info("页面: %s", page_title)
    log.info("URL: %s", url)
    log.info("快照已保存: %s", snapshot)
    for line in diff_lines:
        log.info("%s", line)
    notify.send_webhook(cfg.get("notify", {}), f"网页变化: {title}", url)
    mail_text = f"{title}\n{page_title}\n{url}\n\n" + "\n".join(diff_lines)
    notify.send_email(cfg.get("notify", {}), f"网页变化: {title}", mail_text)
    write_changelog(cfg, url, title, page_title, diff_lines, snapshot)
    return (title, page_title, url, diff_lines, snapshot)


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
    changes = 0
    seen = 0
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
                r = check_page(page, f"{title} :: {page}", cfg, storage)
                if r:
                    changes += 1
                    reports.append(r)
        else:
            seen += 1
            r = check_page(url, title, cfg, storage)
            if r:
                changes += 1
                reports.append(r)
    if changes:
        log.info("本轮完成: 共 %d 处变化 (监控 %d 个页面, 详见 changes.log)", changes, seen)
        if report_file:
            write_report(report_file, reports)
    else:
        log.info("本轮完成: 无变化 (监控 %d 个页面)", seen)


def write_report(path, reports):
    """写入 GitHub Actions 通知报告 (markdown). 云端不存快照, 故不含快照链接."""
    with open(path, "w", encoding="utf-8") as f:
        for title, page_title, url, diff_lines, snapshot in reports:
            f.write(f"## {title}\n")
            if page_title:
                f.write(f"页面: {page_title}\n")
            f.write(f"URL: {url}\n")
            f.write("```diff\n")
            f.write("\n".join(diff_lines) + "\n")
            f.write("```\n\n")


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
        run_once(cfg, storage, report_file=args.report_file)
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
