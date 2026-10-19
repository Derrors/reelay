# 快速开始

从零跑通"监听 → 识别 → 审批 → 转存 → 整理"。开始前请先阅读 [免责声明](../DISCLAIMER.md)。

## 0. 先用模拟模式看一眼（可选，无需任何账号）

```bash
git clone https://github.com/Derrors/reelay && cd reelay
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m reelay.main --dry-run
```

dry-run 使用内存数据库和内置样例消息，走完整条管线后打印每条任务的轨迹，不访问任何网络服务。

## 1. 准备账号与凭证

| 需要什么 | 从哪里来 | 填到哪里 | 必需 |
| --- | --- | --- | --- |
| Telegram `api_id` / `api_hash` | 用个人账号登录 <https://my.telegram.org> → API development tools | `.env`：`REELAY_TG_API_ID`、`REELAY_TG_API_HASH` | 是 |
| 控制 Bot token | 在 Telegram 找 @BotFather 创建一个 Bot | `.env`：`REELAY_CONTROL_BOT_TOKEN` | 推荐 |
| 你的 Telegram 数字 ID | 给 @userinfobot 发任意消息 | `.env`：`REELAY_OWNER_ID` | 推荐 |
| LLM（OpenAI 兼容接口） | 任意兼容服务或本地模型 | `.env`：`REELAY_LLM_BASE_URL`、`REELAY_LLM_API_KEY`、`REELAY_LLM_MODEL` | 推荐 |
| TMDB API Key 或 Read Access Token | <https://www.themoviedb.org/settings/api> | `.env`：`REELAY_TMDB_API_KEY` | 可选 |
| 115 登录态 | 启动后在管理台扫码 | 自动保存到 `data/115_credentials.json` | 是 |

- 不配控制 Bot：审批卡片和告警只写日志，仍可在管理台审批。
- 不配 LLM：解析降级为规则匹配，所有资源都需要人工确认。
- 监听用的是**你的个人账号**，只能读到这个账号本来就能看到的消息，所以先在 Telegram 里关注频道或与 Bot 对话过。

## 2. 安装与配置

### 方式 A：Docker（推荐常驻运行）

见 [Docker 部署](deployment.md)。

### 方式 B：本地运行

```bash
pip install -e ".[all]"
(cd web && npm ci && npm run build)     # 构建管理台，由后端直接托管

cp .env.example .env                    # 填写上表中的凭证
cp config.example.yaml config.yaml      # 先保留示例，下面在管理台里改

python -m reelay.login                  # 交互登录个人 Telegram 账号（验证码/二步密码），生成 data/userbot.session
```

启动两个进程（各开一个终端）：

```bash
python -m reelay.web     # 管理台 http://127.0.0.1:28080
python -m reelay.main    # 监听 + 后台 Worker + 控制 Bot
```

> 管理台默认只监听本机。要从其他设备访问，设置 `REELAY_WEB_HOST=0.0.0.0` 的同时**必须**设置 `REELAY_WEB_PASSWORD`。

## 3. 在管理台完成首次配置

1. **设置 → 115 连接 → 扫码登录**：用 115 App 扫码，cookie 自动保存并探活。可再点"扫码授权"开启文件管理写接口（可选）。
2. **设置 → 基础 → 保存目录**：新增目录别名（如 电影、剧集），点「选择 115 目录」从网盘里选中目标文件夹，CID 自动填入。
3. **设置 → 监听来源 → 添加来源**：填写 Bot 或频道的 `@username`（或数字 ID），确认方式先选"每条都需我确认"。
4. **工具 → 识别测试**：粘贴一条该来源的真实消息，查看解析结果、置信度和决策，确认识别正确。
5. 在页面底部的保存栏保存后无需重启，Worker 约 1 秒内采用新配置并开始监听新来源。

## 4. 验证第一条资源

1. 来源发出新消息后，**待审批**里出现一张资源卡片，可以直接点「转存」或「忽略」，也可以多选批量处理；配置了控制 Bot 的话，Telegram 里同时收到带"✅ 转存 / 🚫 忽略"按钮的卡片。
2. 批准后任务依次经过 `queued → saving → stored → organizing → done`；115 目标目录下出现按媒体库规范命名的目录与文件。
3. 出现问题时：**任务**里点开任意任务可查看完整处理过程；**总览**显示 Worker、消息源、115 凭证状态和进行中的告警；**工具 → 运行日志**可在线查看日志（需配置 `REELAY_LOG_FILE`）。

## 5. 之后可以做的事

- 对信任的来源改用 `approval_mode: auto`，配合 `decision: llm` 和自然语言 `preferences`，只把拿不准的资源留给人工——见 [配置参考](configuration.md)。
- 配置 TMDB，让分类、命名和 `[tmdbid=…]` 更准确。
- 打开 `strm` 扩展生成 STRM，或用 [插件开发指南](plugins.md) 写自己的扩展。
- 通过 [MCP](../README.md#mcp) 让 Claude 等 agent 帮你查询和审批。
