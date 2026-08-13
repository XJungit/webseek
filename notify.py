"""通知: 控制台日志 + webhook (企业微信/钉钉) + SMTP 邮件."""
import logging
import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText

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


def _mail_conf(cfg):
    """读取邮件配置, 优先环境变量(CI 场景), 其次 config.yaml."""
    mail = cfg.get("mail") or {}
    return {
        "host": os.environ.get("MAIL_SMTP_HOST") or mail.get("smtp_host", ""),
        "port": int(os.environ.get("MAIL_SMTP_PORT") or mail.get("smtp_port", 465)),
        "user": os.environ.get("MAIL_SMTP_USER") or mail.get("smtp_user", ""),
        "password": os.environ.get("MAIL_SMTP_PASSWORD") or mail.get("smtp_password", ""),
        "from": os.environ.get("MAIL_FROM") or mail.get("from", ""),
        "to": os.environ.get("MAIL_TO") or mail.get("to", ""),
    }


def send_email(cfg, title, text):
    """SMTP 发送邮件通知. 未配置 SMTP 时静默跳过."""
    conf = _mail_conf(cfg)
    if not (conf["host"] and conf["user"] and conf["password"] and conf["to"]):
        return
    sender = conf["from"] or conf["user"]
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = str(Header(title, "utf-8"))
    msg["From"] = sender
    msg["To"] = conf["to"]
    try:
        if conf["port"] == 465:
            server = smtplib.SMTP_SSL(conf["host"], conf["port"], timeout=20)
        else:
            server = smtplib.SMTP(conf["host"], conf["port"], timeout=20)
            server.starttls()
        with server:
            server.login(conf["user"], conf["password"])
            server.sendmail(sender, [conf["to"]], msg.as_string())
        log.info("邮件已发送: %s", title)
    except smtplib.SMTPException as e:
        log.warning("邮件发送失败: %s", e)
