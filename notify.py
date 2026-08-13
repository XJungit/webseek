"""通知: 控制台日志 + 可选 webhook (企业微信/钉钉/通用)."""
import logging

import requests

log = logging.getLogger("webseek")


def send_webhook(cfg, title, text):
    url = cfg.get("webhook_url", "").strip()
    if not url:
        return
    wtype = cfg.get("webhook_type", "generic")
    if wtype == "wecom":
        payload = {"msgtype": "markdown",
                   "markdown": {"content": f"**{title}**\n{text[:4000]}"}}
    elif wtype == "dingtalk":
        payload = {"msgtype": "markdown",
                   "markdown": {"title": title, "text": f"**{title}**\n{text[:4000]}"}}
    else:
        payload = {"title": title, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        log.info("webhook 发送成功: %s", title)
    except requests.RequestException as e:
        log.warning("webhook 发送失败: %s", e)
