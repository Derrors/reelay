<div align="center">

<img src="docs/assets/logo.svg" width="88" alt="Reelay">

# Reelay

**把 Telegram 里刷到的影视资源，变成 115 网盘里整理好的媒体库。**

自托管 · AI 识别 · 你来拍板 · 转存必核验

[![Release](https://img.shields.io/github/v/release/Derrors/reelay?color=1F9E8F&label=release)](https://github.com/Derrors/reelay/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/Derrors/reelay/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white)](https://github.com/Derrors/reelay/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/ghcr.io-derrors%2Freelay-1F9E8F?logo=docker&logoColor=white)](https://github.com/Derrors/reelay/pkgs/container/reelay)
![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
[![License](https://img.shields.io/badge/license-MIT-64748B)](LICENSE)

[快速开始](docs/quickstart.md) · [部署与运维](docs/deployment.md) · [配置参考](docs/configuration.md) · [Agent 接入](docs/agents.md) · [插件开发](docs/plugins.md) · [架构设计](docs/architecture.md)

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/flow-dark.svg">
  <img src="docs/assets/flow-light.svg" width="860" alt="监听 → 识别 → 确认 → 转存 → 入库">
</picture>

</div>

<br>

你关注的频道和 Bot 每天刷出一堆资源，真正想要的就那几部。Reelay 用你自己的 Telegram 账号替你盯着：AI 读懂每条消息是哪部片、第几季第几集、什么版本，再由 TMDB 和分享里的真实文件替它把关；你点一下「转存」，它就把分享存进你的 115，按 Jellyfin / Emby 的规范改名归类。下次打开媒体服务器，新的一集已经在那里了。

> **Reel**（胶片）+ **Relay**（接力）：替你把片子从消息流接力到媒体库。

## ✨ 亮点

<table>
<tr>
<td width="33%" valign="top">

### 🧠 读得懂消息
LLM 抽取片名、季集、清晰度、字幕、音轨与 HDR，TMDB 校正成规范片名。分享码只由代码提取，AI 拿不准就交给你。

</td>
<td width="33%" valign="top">

### ✋ 你说了算
每条资源附建议和理由，一键批准、批量处理；也可以用一句话写偏好，AI 起草成规则，你确认后才生效。

</td>
<td width="33%" valign="top">

### 🛡️ 转存不出错
独占暂存目录、先落检查点再调接口、按大小与 SHA1 读回核验。结果不明时只核验、绝不重发，115 落地慢也会自动复查。

</td>
</tr>
<tr>
<td valign="top">

### 🗂️ 入库即整理
按二级分类（如「剧集 / 国产剧集」）、媒体类型或不分类存放，模板命名带 `[tmdbid=…]`，同名冲突可选转人工、跳过、并存或洗版。

</td>
<td valign="top">

### 📺 追剧不用盯
订阅一部剧，之后的新集自动放行；多季合集按季拆开整理，已入库的集自动跳过，缺集和断档会出现在日报里。

</td>
<td valign="top">

### 🧩 随处可用
Web 管理台、Telegram 控制 Bot、给 Claude 等 Agent 用的 MCP；Provider / Hook / Event 三类扩展点，一个目录就是一个插件。

</td>
</tr>
</table>

## 💬 在 Telegram 里是这样的

<table>
<tr>
<th width="50%">待审批卡片</th>
<th width="50%">入库通知</th>
</tr>
<tr>
<td valign="top">

```text
📺 漫长的季节 (2023)
🗂 第 1 季 · E01-E10
🎞 2160p · 简体中字 · 国语 · 杜比视界
📦 38.6 GB

📡 来源 @example_channel
📂 存到 /剧集/国产剧集

   [ ✅ 转存 ]   [ 🚫 忽略 ]
```

</td>
<td valign="top">

```text
✅ 已入库 · 漫长的季节 (2023)
🗂 第 1 季 · E01-E10
🎞 2160p · 简体中字 · 国语 · 杜比视界
📂 /剧集/国产剧集/漫长的季节 (2023) [tmdbid=…]/Season 01
📄 10 个文件：漫长的季节 - S01E01.mkv、
   漫长的季节 - S01E02.mkv 等
```

</td>
</tr>
</table>

同样的待审批也在管理台的收件箱里：海报、季集、版本对比与建议一目了然，可以多选批量处理。不想审批的来源可以改为自动转存，Reelay 会在你确认得够多之后主动建议。

## 🚀 快速开始

只需要 Docker 和一个 `compose.yaml`，镜像支持 amd64 与 arm64：

```bash
mkdir reelay && cd reelay
curl -fsSLO https://raw.githubusercontent.com/Derrors/reelay/main/compose.yaml
docker compose pull
docker compose run --rm --no-deps --user root worker chown -R 10001:10001 /app/data
docker compose up -d web          # 管理台 http://127.0.0.1:28080
```

在管理台「设置」里填好 Telegram、AI 与 115，然后登录个人 Telegram 账号并启动监听：

```bash
docker compose run --rm --no-deps -it worker python -m reelay.login
docker compose up -d
```

账号从哪里申请、第一条资源怎么验证，见 [快速开始](docs/quickstart.md)。

<details>
<summary><b>不用 Docker，或者先在本机模拟体验</b></summary>

<br>

模拟模式使用内存数据库和内置样例消息走完整条流程，不需要任何账号：

```bash
git clone https://github.com/Derrors/reelay && cd reelay
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m reelay.main --dry-run
```

在本机直接运行：

```bash
pip install -e ".[all]"
(cd web && npm ci && npm run build)
python -m reelay.web       # 管理台
python -m reelay.login     # 登录个人 Telegram 账号
python -m reelay.main      # 监听与后台任务（另开终端）
```

</details>

## 🎞️ 入库后的样子

默认按二级分类存放（也可以按媒体类型或不分类），命名模板可在管理台修改：

```text
电影/
└── 国产电影/
    └── 流浪地球2 (2023) [tmdbid=…]/
        └── 流浪地球2 (2023) - 4K.mkv
剧集/
└── 国产剧集/
    └── 漫长的季节 (2023) [tmdbid=…]/
        └── Season 01/
            ├── 漫长的季节 - S01E01.mkv
            └── 漫长的季节 - S01E02.mkv
动漫/
└── 日本番剧/
    └── …
其他/
└── 纪录影片/
    └── …
```

## 🧭 设计理念

**AI 提议 → 事实源校验 → 人工裁决 → 裁决回流给 AI。**

AI 负责读消息、给建议，TMDB 与分享里的真实文件负责把关，拿不准的交给你；你的每次裁决都会让下次识别更准。转存、改名、去重始终由确定性代码完成并读回核验。详见 [架构设计](docs/architecture.md)。

## 📚 文档

| 文档 | 内容 |
| --- | --- |
| [快速开始](docs/quickstart.md) | 准备账号、启动、首次配置，验证第一条资源 |
| [部署与运维](docs/deployment.md) | Docker 部署、访问与安全、升级回滚、备份、健康检查与故障恢复 |
| [配置参考](docs/configuration.md) | 系统配置、插件配置、密钥与运行文件的完整说明 |
| [Agent 接入](docs/agents.md) | 用 MCP 让 Claude 等 Agent 查询和审批，控制 Bot 的对话操作 |
| [插件开发](docs/plugins.md) | 扩展点、PipelineContext、调用 AI、元数据补全约定 |
| [架构设计](docs/architecture.md) | 状态机、AI 与规则的分工、转存与恢复边界 |
| [更新日志](CHANGELOG.md) | 每个版本的变化 |

## 📌 项目状态

> [!NOTE]
> 早期版本（Alpha），作者日常自用，人工审批的完整流程在真实账号上持续运行。自动审批、多来源与容器长期运行的验证还不充分，欢迎反馈。支持 macOS / Linux，Windows 请使用 Docker。

> [!WARNING]
> Reelay 不提供、不索引任何内容来源，只处理你自己的账号能看到的消息，转存到你自己的网盘。以个人账号自动化监听 Telegram 可能导致**封号**，频繁调用 115 接口可能触发**风控**。使用前请阅读 [免责声明](DISCLAIMER.md)。

## 🤝 参与

欢迎 Issue 与 PR，开发环境与提交规范见 [CONTRIBUTING](CONTRIBUTING.md)；安全问题请按 [SECURITY](SECURITY.md) 私下报告。

<details>
<summary>English</summary>

<br>

Reelay is a self-hosted pipeline that watches the Telegram channels and bots your own account follows. An LLM recognizes media releases and 115 cloud share links, TMDB and the actual share listing verify them, and—after your approval—Reelay saves them into your own 115 drive, organized for Jellyfin / Emby. It ships with a web console, a Telegram control bot, an MCP server for AI agents, and a plugin system. Multi-arch images are published at `ghcr.io/derrors/reelay`. The UI and docs are in Chinese.

</details>

## License

[MIT](LICENSE) © Derrors
