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

**简体中文** · [English](README.en.md)

[快速开始](docs/quickstart.md) · [部署与运维](docs/deployment.md) · [配置参考](docs/configuration.md) · [Telegram 与 Agent](docs/agents.md) · [插件开发](docs/plugins.md) · [架构设计](docs/architecture.md)

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/flow-dark.svg">
  <img src="docs/assets/flow-light.svg" width="860" alt="监听 → 识别 → 确认 → 转存 → 入库">
</picture>

</div>

<br>

你关注的频道和 Bot 每天刷出一堆资源，真正想要的就那几部。Reelay 用你自己的 Telegram 账号替你盯着：AI 读懂每条消息是哪部片、第几季第几集、什么版本，再由 TMDB 和分享里的真实文件替它把关；你在 Telegram 上点一下「转存」，它就把分享存进你的 115，按 Jellyfin / Emby 的规范改名归类。下次打开媒体服务器，新的一集已经在那里了。

在网页管理台配置一次，之后审批、修正、追剧、看日报都在 Telegram 上完成。

> **Reel**（胶片）+ **Relay**（接力）：替你把片子从消息流接力到媒体库。

## ✨ 亮点

<table>
<tr>
<td width="33%" valign="top">

### 🧠 读得懂消息
AI 识别片名、季集与版本，TMDB 校正片名；AI 不可用时按规则兜底。

</td>
<td width="33%" valign="top">

### 📱 在 Telegram 上搞定
一键转存、换目录、追剧；识别错了，发一句话就能改。

</td>
<td width="33%" valign="top">

### 🛡️ 转存不出错
先存检查点再调接口，事后读回核验；结果不明只核验，绝不重发。

</td>
</tr>
<tr>
<td valign="top">

### 🗂️ 入库即整理
自动分类、按模板改名；库里已有时可转人工、跳过、并存或洗版。

</td>
<td valign="top">

### 📺 追剧不用盯
订阅后新集自动转存，每天提醒缺集，播完自动结束。

</td>
<td valign="top">

### 🧩 随处可用
Web 管理台、Telegram Bot、MCP 三种入口；一个目录就是一个插件。

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
🖼 （TMDB 剧照）
📺 漫长的季节 (2023) · S01 E01-E10
The Long Season

🌟 评分：8.6
🎭 类型：剧情 · 悬疑 · 犯罪
🌍 地区：中国大陆
👥 主演：范伟、秦昊、陈明昊
🖥 分辨率：2160p · 杜比视界
💬 字幕：简体中字
📦 大小：38.6 GB

📖 简介：
▍ 1997 年，出租车司机王响……（可展开）

📂 存到：/剧集/国产剧集
📡 来源：@example_channel

[ ✅ 转存 ]    [ 🚫 忽略   ]
[ 📂 换目录 ]  [ ✏️ 修正    ]
[ 🔔 追剧 ]    [ ⛔ 不再接收 ]
[ 🎬 TMDB 详情             ]
```

</td>
<td valign="top">

```text
🖼 （TMDB 剧照）
✅ 已入库

📺 漫长的季节 (2023) · S01 E01-E10
The Long Season

🌟 评分：8.6
🎭 类型：剧情 · 悬疑 · 犯罪
🖥 分辨率：2160p · 杜比视界
📦 大小：38.6 GB

📂 位置：/剧集/国产剧集/漫长的季节 (2023)
   [tmdbid=…]/Season 01
📄 文件：10 个，漫长的季节 - S01E01 - 第 1 集
   - 2160p H265.mkv、漫长的季节 - S01E02 -
   第 2 集 - 2160p H265.mkv 等

[ 🎬 TMDB 详情 ]
```

</td>
</tr>
</table>

不打开网页也能做的事：

| 场景 | 在 Telegram 上怎么做 |
| --- | --- |
| 识别错了 | 卡片上点「✏️ 修正」，发一句 `片名 繁花 第 2 季`，没写的不变；也可以重新识别 |
| 转存或整理停下来 | 「需要处理」的提醒按情况给出按钮：核验转存、按已到的继续、重新整理、删除或覆盖、重试 |
| 积压了一堆 | `/pending` 每条点开卡片处理，或一键全部批准、全部忽略 |
| 追剧 | `/subs` 看已入库与缺集，取消、继续订阅，处理订阅建议；缺集提醒可标「不用等了」 |
| 不想再收某部 | 卡片上点「⛔ 不再接收」，`/ignored` 随时恢复 |
| 看看运行得怎样 | `/status` 运行概况；每天还会收到日报 |
| 换个说法 | 配置了 AI 时直接说：「待审批里有什么」「把《X》这几集都批了，存到动漫」 |

115 登录失效后的扫码和各项配置在网页管理台完成。详见 [Telegram 与 Agent](docs/agents.md)。

## 🚀 快速开始

只需要 Docker 和一个 `compose.yaml`，镜像支持 amd64 与 arm64：

```bash
mkdir reelay && cd reelay
curl -fsSLO https://raw.githubusercontent.com/Derrors/reelay/main/compose.yaml
docker compose pull
docker compose up -d web          # 管理台 http://127.0.0.1:28080
```

在管理台「设置」里填好 Telegram、AI 与 115，然后登录个人 Telegram 账号并启动监听：

```bash
docker compose run --rm --no-deps -it worker python -m reelay.login
docker compose up -d
```

接着在「设置 → 监听来源」添加要盯的频道或 Bot：贴几条样例消息，AI 会起草这个来源的规则，在卡片上点「测试识别」看看效果。账号从哪里申请、第一条资源怎么验证，见 [快速开始](docs/quickstart.md)。

<details>
<summary><b>不用 Docker，或者先在本机模拟体验</b></summary>

<br>

模拟模式用内置配置、样例消息和内存里的网盘走完整条流程，不读本机的配置与密钥，也不需要任何账号：

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

默认按二级分类存放（也可以按媒体类型或不分类），命名模板可在管理台修改，改完点「预览整理效果」就能看到结果：

```text
电影/
└── 国产电影/
    └── 流浪地球2 (2023) [tmdbid=…]/
        └── 流浪地球2 (2023) - 2160p H265.mkv
剧集/
└── 国产剧集/
    └── 漫长的季节 (2023) [tmdbid=…]/
        └── Season 01/
            ├── 漫长的季节 - S01E01 - 第 1 集 - 2160p H265.mkv
            └── 漫长的季节 - S01E02 - 第 2 集 - 2160p H265.mkv
动漫/
└── 日本番剧/
    └── …
其他/
└── 纪录影片/
    └── …
```

## 🧭 设计理念

**AI 提议 → 事实源校验 → 人工裁决 → 裁决回流给 AI。**

AI 负责读消息、给建议，TMDB 与分享里的真实文件负责把关，拿不准的交给你；你在卡片上的每次修正都会让同一来源的下次识别更准。转存、改名、去重始终由确定性代码完成并读回核验。详见 [架构设计](docs/architecture.md)。

## 📚 文档

| 文档 | 内容 |
| --- | --- |
| [快速开始](docs/quickstart.md) | 准备账号、启动、首次配置，验证第一条资源 |
| [部署与运维](docs/deployment.md) | Docker 部署、访问与安全、升级回滚、备份、健康检查与故障恢复 |
| [配置参考](docs/configuration.md) | 系统配置、来源识别规则、插件配置、密钥与运行文件的完整说明 |
| [Telegram 与 Agent](docs/agents.md) | 控制 Bot 的按钮、命令与对话；用 MCP 让 Claude 等 Agent 查询和审批 |
| [插件开发](docs/plugins.md) | 扩展点、PipelineContext、调用 AI、元数据补全约定 |
| [架构设计](docs/architecture.md) | 状态机、AI 与规则的分工、转存与恢复边界 |
| [更新日志](CHANGELOG.md) | 每个版本的变化 |

## 📌 项目状态

> [!NOTE]
> 测试版（Beta），功能已经齐备，作者日常自用，人工审批的完整流程在真实账号上持续运行；自动审批、多来源与容器长期运行还在积累验证，1.0 之前配置与数据格式仍可能调整，欢迎反馈。支持 macOS / Linux，Windows 请使用 Docker。

> [!WARNING]
> Reelay 不提供、不索引任何内容来源，只处理你自己的账号能看到的消息，转存到你自己的网盘。以个人账号自动化监听 Telegram 可能导致**封号**，频繁调用 115 接口可能触发**风控**。使用前请阅读 [免责声明](DISCLAIMER.md)。

## 🤝 参与

欢迎 Issue 与 PR，开发环境与提交规范见 [CONTRIBUTING](CONTRIBUTING.md)；安全问题请按 [SECURITY](SECURITY.md) 私下报告。

## License

[MIT](LICENSE) © Derrors
