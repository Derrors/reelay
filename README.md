<div align="center">

<img src="docs/assets/logo.svg" width="88" alt="Reelay">

# Reelay

**把 Telegram 消息里的影视资源，转存成 115 网盘中整理好的媒体库。**

监听来源 · AI / 规则识别 · 审批与追剧 · 转存核验 · 自动整理

[![Release](https://img.shields.io/github/v/release/Derrors/reelay?color=1F9E8F&label=release)](https://github.com/Derrors/reelay/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/Derrors/reelay/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white)](https://github.com/Derrors/reelay/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/ghcr.io-derrors%2Freelay-1F9E8F?logo=docker&logoColor=white)](https://github.com/Derrors/reelay/pkgs/container/reelay)
![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
[![License](https://img.shields.io/badge/license-MIT-64748B)](LICENSE)

**简体中文** · [English](README.en.md)

[在线文档](https://derrors.github.io/reelay/) · [快速开始](https://derrors.github.io/reelay/quickstart.html) · [使用指南](https://derrors.github.io/reelay/user-guide.html) · [版本发布](https://github.com/Derrors/reelay/releases) · [问题反馈](https://github.com/Derrors/reelay/issues)

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/flow-dark.svg">
  <img src="docs/assets/flow-light.svg" width="860" alt="监听 Telegram 消息 → 识别资源 → 审批或按规则放行 → 转存到 115 → 整理入库">
</picture>

</div>

## 简介

Reelay 是一个**自托管的 Telegram → 115 资源整理工具**，适合在 NAS 或家用服务器上运行。它用你自己的 Telegram 账号监听指定来源，识别片名、季集和版本，经人工确认或按规则放行后转存、核验并整理到自己的 115 网盘。

在网页管理台配置，在网页或 Telegram 中审批、修正和追剧。**Reel**（胶片）+ **Relay**（接力）：把资源从消息流接力到媒体库。

> 不提供资源搜索、全网补集、播放器、网盘挂载或 STRM 生成。媒体服务器如何读取 115 文件，需要另行配置。[了解完整能力与边界 →](https://derrors.github.io/reelay/introduction.html)

## 功能特性

- **识别与筛选**：AI / 规则识别，TMDB 匹配，支持 115 分享和电驴链接，按来源设置过滤条件。
- **审批可控**：逐条或批量确认，也可按规则自动放行；识别有误可修正或重新识别。
- **按季追剧**：订阅新集放行，启用追更插件后查看缺集与提醒；v1.5.0 起支持仅处理订阅模式。
- **转存核验**：先记录检查点，再执行转存；结果不明先核验，不盲目重复提交。电驴交给 115 云下载，不下载到宿主机。
- **自动整理**：分类、命名模板、整理预览与重复版本处理，方便对接 Jellyfin / Emby 的命名习惯。
- **多种入口**：Web 管理台、Telegram 控制 Bot、MCP；支持插件扩展与 amd64 / arm64 Docker 部署。

## 快速开始

准备可持续运行的设备、Docker Compose **v2.24+**、Telegram 个人账号与 API ID / API Hash、115 账号。AI、TMDB 和控制 Bot 的配置要求见 [完整准备清单](https://derrors.github.io/reelay/quickstart.html#开始前的准备)。

**1. 启动管理台：**

```bash
mkdir reelay && cd reelay
curl -fsSLO https://raw.githubusercontent.com/Derrors/reelay/main/compose.yaml
docker compose pull
docker compose up -d web
```

**2. 打开 [http://127.0.0.1:28080](http://127.0.0.1:28080)**，设置管理台密码，并在「设置」中配置网络、Telegram、AI / TMDB 与 115。

默认只监听部署机器本机；NAS 或局域网访问请按 [打开管理台](https://derrors.github.io/reelay/quickstart.html#2-打开管理台) 设置，不要直接暴露到公网。

**3. 在同一目录登录 Telegram 个人账号并启动后台：**

```bash
docker compose run --rm --no-deps -it worker python -m reelay.login
docker compose up -d
```

**4. 配置保存目录与监听来源**，先用「人工确认 + 规则判断」测试一条有权保存的消息，再等待新消息、审批并确认文件入库。

详细步骤、成功标志、本机安装与离线模拟，统一见 **[快速开始](https://derrors.github.io/reelay/quickstart.html)**。

## 在线文档

| 我想做什么 | 阅读入口 |
| --- | --- |
| 配置来源、审批、追剧与整理 | [使用指南](https://derrors.github.io/reelay/user-guide.html) |
| 排查页面、消息或转存问题 | [常见问题](https://derrors.github.io/reelay/faq.html) |
| 查询参数、升级与备份 | [配置参考](https://derrors.github.io/reelay/configuration.html) · [部署与运维](https://derrors.github.io/reelay/deployment.html) |
| 使用 Bot、自然语言或 MCP | [Telegram 与 Agent](https://derrors.github.io/reelay/agents.html) |
| 了解内部机制、编写扩展 | [架构设计](https://derrors.github.io/reelay/architecture.html) · [插件开发](https://derrors.github.io/reelay/plugins.html) |

## 使用须知

- 文档跟随源码更新，**未发布功能不代表正式镜像已支持**；以 [Releases](https://github.com/Derrors/reelay/releases) 和 [更新日志](CHANGELOG.md) 为准。
- AI 功能会向你配置的接口发送相关内容；第三方服务可能收费。数据目录包含密钥和登录会话，请妥善保管、完整备份，不要公开上传。
- 只处理有权使用的内容；自动化可能触发 Telegram 账号限制或 115 风控。使用前请阅读 [免责声明](DISCLAIMER.md)。

## 参与贡献

欢迎提交 Issue 或 PR。开发规范见 [贡献指南](CONTRIBUTING.md)，安全问题请按 [安全政策](SECURITY.md) 私下报告；反馈时不要附带密钥、Cookie 或会话文件。

[MIT](LICENSE) © Derrors
