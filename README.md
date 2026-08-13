# webseek - 网页变化实时监控

监控网站内容变化,发现变化时输出 diff、保存快照并可选推送通知。

## 快速开始

```bash
pip install -r requirements.txt

python monitor.py --once     # 单轮抓取 (首次运行建立基线)
python monitor.py            # 持续轮询监控 (按 config.yaml 的 interval)
```

## 配置 (config.yaml)

- `interval_seconds`: 轮询间隔(秒), 默认 600
- `targets`: 监控目标
  - 普通目标: `url` + `title`
  - 整站文档: `sitemap: true` 将从 sitemap.xml 自动发现所有页面
- `notify.webhook_url`: 企业微信/钉钉机器人 URL, 留空仅控制台输出

## 工作原理

1. 抓取页面 → 提取正文(标题 + main 文本 + SSR 数据), 忽略导航/版权/脚本噪声
2. 对规范化文本计算 SHA256, 与上次记录对比
3. 变化时: 输出 unified diff、保存快照到 `snapshots/`、触发 webhook
4. 状态持久化在 `state.json`, 程序重启后自动继续对比

## 特性

- 自动编码探测, 正确处理无 charset 声明的页面
- 支持 Next.js `__next_f.push` 流式数据中的关键公告文本(过滤时间戳/路径噪声)
- 自动重试 + 失败不误报
- 新页面首次访问自动登记, 不触发"变化"

## 长期运行

Windows 可用计划任务/`start /b` 后台运行; Linux 建议 systemd 或 `nohup`。