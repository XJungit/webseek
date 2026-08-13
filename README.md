# webseek - 网页变化实时监控

监控网站内容变化,发现变化时自动通知(邮件 / GitHub Issue / 企业微信 / 钉钉)。

本仓库内置 **GitHub Actions 定时任务**:每 10 分钟自动抓取 DeepSeek 官网与 API 中文文档(64 个页面),检测到内容变化时自动创建 Issue 通知,并记录变更历史。

## 别人如何订阅变化

任何 GitHub 用户都可以订阅本仓库的变化通知:

1. 打开 https://github.com/XJungit/webseek
2. 点击右上角 **Watch → All activity**
3. 之后每次检测到网页变化(自动创建 Issue 时),会收到 GitHub 通知邮件

## 你如何收到通知

| 方式 | 说明 | 配置 |
|---|---|---|
| GitHub Issue + 邮件 | 变化时自动创建 Issue,订阅者可收到邮件 | 无需配置 |
| SMTP 邮件 | 直接发送到指定邮箱,正文含完整 diff | 见下方 Secrets 配置 |
| 企业微信/钉钉 webhook | 推送消息到群 | config.yaml 填 webhook_url |

## GitHub Actions 自动监控(推荐)

fork 本仓库后自动生效(无需配置):

```yaml
# .github/workflows/monitor.yml
on:
  schedule:
    - cron: '*/10 * * * *'   # 每 10 分钟
```

每次运行:抓取 → 对比哈希 → 有变化则创建 Issue + 提交状态;无变化零提交。

### 可选:SMTP 邮件通知(需 GitHub Secrets)

在仓库 **Settings → Secrets and variables → Actions** 添加:

| Secret | 值 |
|---|---|
| `MAIL_SMTP_HOST` | 如 `smtp.qq.com` |
| `MAIL_SMTP_PORT` | `465`(SSL)或 `587`(STARTTLS) |
| `MAIL_SMTP_USER` | 邮箱账号 |
| `MAIL_SMTP_PASSWORD` | 邮箱授权码(非登录密码) |
| `MAIL_FROM` | 发件邮箱 |
| `MAIL_TO` | 收件邮箱(可多个,逗号分隔) |

QQ 邮箱授权码:设置 → 账户 → 开启 POP3/SMTP 服务 → 短信验证后获取。

## 本地运行

```bash
pip install -r requirements.txt
python monitor.py --once     # 单轮抓取
python monitor.py            # 持续轮询(默认每 10 分钟)
```

## 配置 (config.yaml)

- `interval_seconds`: 轮询间隔
- `targets`: 监控目标(普通 URL 或 `sitemap: true` 自动发现整站)
- `notify.webhook_url`: 企业微信/钉钉机器人
- `notify.mail`: SMTP 邮件(或环境变量 `MAIL_*`)

## 工作原理

1. 抓取页面 → 提取正文(标题 + main 文本 + Next.js SSR 数据),忽略导航/版权/脚本噪声
2. 规范化文本计算 SHA256,与上次基线对比
3. 变化时:输出 diff、保存快照到 `snapshots/`、追加 `changes.log`、发送通知
4. 状态持久化在 `state.json`(无变化不写盘,零重复数据)

## 特性

- 自动编码探测,支持无 charset 声明的页面
- 兼容 Next.js `__next_f.push` 流式数据(公告文本提取,过滤时间戳噪声)
- sitemap 自动发现文档站全部页面(新增页面自动纳入)
- 自动重试,抓取失败不误报
- GitHub Actions 无变化轮次零提交

## 部署到自己的目标

修改 `config.yaml` 的 `targets` 即可监控任意网站:

```yaml
targets:
  - url: "https://example.com/"
    title: "示例站点"
  - url: "https://example.com/sitemap.xml"
    title: "示例文档站"
    sitemap: true
```

## 长期运行

- GitHub Actions:自动运行,无需服务器
- 本地:Windows 计划任务 / Linux systemd / `nohup`