# webseek - 网页变化实时监控

监控网站内容变化,发现变化时自动通知(邮件 / GitHub Issue / 企业微信 / 钉钉)。

本仓库内置 **GitHub Actions 定时任务**:每 10 分钟自动抓取 DeepSeek 官网与 API 中文文档(64 个页面),检测到内容变化时自动创建 Issue 通知,并记录变更历史。

## 别人如何订阅变化

任何 GitHub 用户都可以订阅本仓库的变化通知:

1. 打开 https://github.com/XJungit/webseek
2. 点击右上角 **Watch → All activity**
3. 之后每次检测到网页变化(自动创建 Issue 时),会收到 GitHub 通知邮件

## 你如何收到通知

| 方式 | 本地运行 | GitHub Actions | 配置 |
|---|---|---|---|
| 控制台 + changes.log + 快照 | ✅ | — | 无需配置 |
| GitHub Issue + Watch 邮件 | — | ✅ | Watch 仓库(默认) |
| SMTP 邮件 | ✅ | ✅ | 配置 Secrets/环境变量 |
| 企业微信/钉钉 webhook | ✅ | ✅ | config.yaml 填 webhook_url |

## 本地运行

```bash
pip install -r requirements.txt
python monitor.py --once     # 单轮抓取, 首次自动建立基线
python monitor.py            # 持续轮询(默认每 10 分钟)
```

- 变化时:控制台输出 diff + 追加 `changes.log` + 保存 `snapshots/` 快照
- 想本地也收邮件/webhook:配置 `notify`(见下),本地读取环境变量或 config.yaml

## GitHub Actions 自动监控(可选, 推荐云上方案)

仓库内置 `.github/workflows/monitor.yml`,**每 10 分钟**自动运行,无需本地服务器:

```yaml
on:
  schedule:
    - cron: '*/10 * * * *'
  workflow_dispatch:          # 也可手动触发
```

运行机制:
- 抓取 → 对比 SHA256 基线
- **有变化**:创建 Issue(含页面标题 + URL + diff)、提交状态
- **无变化**:零提交
- **运行失败**:自动创建 Issue(通过 Watch 邮件告知,无需 SMTP)
- 状态仅提交 `state.json` + `changes.log`(带 `[skip ci]`,不污染主分支);快照只在本地运行时保留

**fork/clone 到自己账号后启用**:
1. 在自己仓库 **Actions → webseek-monitor → Enable**
2. Watch → All activity 即可收到变化邮件
3. 想收 SMTP 邮件:Settings → Secrets 添加 `MAIL_*`

### SMTP 邮件通知配置

本地:在 `config.yaml` 的 `notify.mail` 填写,或设环境变量 `MAIL_SMTP_HOST` 等。
Actions:在仓库 **Settings → Secrets and variables → Actions** 添加:

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