<div align="center">

# Reelay

**自托管的 Telegram → 115 网盘自动转存**

监听你关注的资源频道与 Bot，用 AI 识别影视资源和 115 分享链接，<br>
经你确认后转存进自己的网盘，并按 Jellyfin / Emby 规范整理入库。

[![持续集成](https://github.com/Derrors/reelay/actions/workflows/ci.yml/badge.svg)](https://github.com/Derrors/reelay/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab.svg)

[快速开始](docs/quickstart.md) · [配置参考](docs/configuration.md) · [插件开发](docs/plugins.md) · [架构设计](docs/architecture.md) · [Docker 部署](docs/deployment.md) · [免责声明](DISCLAIMER.md)

</div>

> **Reel**（影视胶片）+ **Relay**（接力转发）

*Reelay is a self-hosted pipeline that watches Telegram channels and bots your own account follows, uses an LLM to recognize media releases and 115 cloud share links, and—after your approval—saves them into your own 115 drive, organized for Jellyfin/Emby. It ships with a web console, a Telegram control bot, an MCP server for AI agents, and a plugin system. The UI and docs are in Chinese.*

## 特性

- **监听**：以个人账号（userbot）同时监听多个频道/Bot，读取超链接中的真实地址；重连后按消息 ID 补拉停机期间的消息。
- **AI 提议，事实源校验，人工裁决，裁决结果反馈给 AI**：AI 负责理解与提议，TMDB、分享清单与规则负责把关，拿不准的交给你；你的批准、忽略与修正会回流成识别示例、偏好参考和「改为自动转存」的建议。
- **识别**：LLM 结构化输出 + 本地严格校验，一并抽取字幕、音轨、HDR、片源等版本信息；调用前确定性预过滤、相同正文缓存、瞬时错误重试，失败降级规则解析并转人工。分享码只由代码提取，不信任 AI。
- **决策**：人工确认 / 自动转存 / 静默转存三种模式；用一句话描述偏好，AI 起草成过滤、分类与版本偏好规则，确认后写入；追剧订阅让后续新集自动放行；置信度由可核对的信号计算，拿不准的一律交给你。
- **少审批**：待审批附建议与理由、同一集多个版本标出推荐版本、可按建议批量处理；分享文件按规则认不出时先让 AI 给方案并经规则校验，不过才转人工。
- **安全转存**：独占暂存目录、发送前持久化检查点、读回按大小与 SHA1 核验；结果不明时只核验绝不重发；三层去重，剧集按（季, 集）增量追更，跨多季的合集按季转存与整理。
- **整理入库**：TMDB 匹配规范标题与 `[tmdbid=…]`，MoviePilot 风格二级分类，Jinja2 命名模板，同名冲突可选转人工 / 跳过 / 并存 / 洗版。
- **随处操作**：Web 管理台（待审批收件箱与批量审批、海报墙媒体库、追剧订阅、实时推送、统计与 AI 调用记录，支持深色模式与手机）、可以用一句话查询和操作的 Telegram 控制 Bot（写操作先确认）、[MCP](#mcp) 供 Claude 等 agent 调用。
- **长期运行**：SQLite WAL 持久队列与版本化迁移，重启可恢复；115 限流与风控冷却，凭证失效自动暂停并告警；失败时说明原因与该用的操作；日报与缺集追更提醒；来源质量统计。
- **可扩展**：Provider / Hook / Event 三类扩展点，外部插件放进目录即加载，见 [插件开发指南](docs/plugins.md)。

## 快速开始

先用模拟模式体验整条管线（不需要任何账号）：

```bash
git clone https://github.com/Derrors/reelay && cd reelay
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m reelay.main --dry-run
```

接入真实账号：

```bash
pip install -e ".[all]"
(cd web && npm ci && npm run build)
python -m reelay.web          # 管理台 http://127.0.0.1:28080：在「设置」填写凭证
python -m reelay.login        # 登录个人 Telegram 账号
python -m reelay.main         # 监听 + Worker（另开终端）
```

所有配置与密钥都在管理台修改，保存在 `data/` 目录（`data/config.yaml` 与 `data/secrets.json`）。然后在管理台扫码登录 115、选择保存目录、添加监听来源。完整步骤见 [快速开始](docs/quickstart.md)；常驻运行推荐 [Docker 部署](docs/deployment.md)：每个版本都发布 `ghcr.io/derrors/reelay` 镜像（amd64 / arm64），只需一个 `compose.yaml`。

## MCP

```bash
pip install -e ".[agent]"
claude mcp add reelay -- /path/to/reelay/.venv/bin/python -m reelay.mcp_server
```

提供运行状态、任务查询、待处理列表、任务详情、目录别名、媒体库检索，以及审批 / 忽略 / 重试（批准后由运行中的 Worker 执行真实转存）。设置 `REELAY_MCP_READONLY=1` 只暴露查询工具。

## 项目结构

```
reelay/
├── core/          # 内核：契约、管线、状态机与持久层、注册机、置信度、TMDB、115 凭证
├── providers/     # 内置 Provider：Telegram 源、LLM/规则解析、决策、115 存储、通知
├── plugins/       # 内置扩展：分类路由、TMDB 补全、整理、日报、缺集追更
├── control/       # Telegram 控制 Bot 与对话助手
├── web.py         # 管理 API（前端在 web/）
├── mcp_server.py  # MCP server
└── eval.py        # 本地评测：用人工裁决样本对比提示词与模型
examples/plugins/  # 外部插件示例
```

## 开发

```bash
pip install -e ".[all,dev]"
ruff check . && python -m unittest discover -s tests
cd web && npm ci && npm run typecheck && npm run lint:eslint && npm test && npm run build
```

欢迎贡献，请先阅读 [CONTRIBUTING](CONTRIBUTING.md)；安全问题请按 [SECURITY](SECURITY.md) 私下报告。版本变化见 [CHANGELOG](CHANGELOG.md)。

## 项目状态

早期版本（Alpha）。真实账号已跑通 单来源、剧集单集 的完整链路；电影、多集合集、自动审批、多来源、长期断线与 Docker 部署尚缺真实环境验证，欢迎反馈。目前仅支持 macOS / Linux（Windows 请使用 Docker）。

## 免责声明

Reelay 不提供、不索引任何内容来源，只处理你自己的账号能看到的消息，并转存到你自己的网盘。以个人账号自动化监听 Telegram 可能导致**封号**，高频调用 115 接口可能触发**风控**。使用前请阅读 [免责声明](DISCLAIMER.md)。

## License

[MIT](LICENSE) © Derrors
