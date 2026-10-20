# 快速开始

从零到第一条资源整理入库。开始前请先读一遍 [免责声明](../DISCLAIMER.md)。

## 1. 准备账号

| 需要什么 | 从哪里来 | 必需 |
| --- | --- | --- |
| Telegram `api_id` / `api_hash` | 用个人账号登录 <https://my.telegram.org> → API development tools | 是 |
| 115 账号 | 启动后在管理台扫码登录 | 是 |
| 控制 Bot Token | 在 Telegram 找 @BotFather 创建一个 Bot | 推荐 |
| 你的 Telegram 数字 ID | 给 @userinfobot 发任意消息 | 推荐 |
| AI 服务（OpenAI 兼容接口） | 任意兼容服务或本地模型 | 推荐 |
| TMDB API Key 或 Read Access Token | <https://www.themoviedb.org/settings/api> | 可选 |

缺了推荐项也能用，只是少一些自动化：

- **没有控制 Bot**：审批卡片和告警只写日志，在管理台审批；
- **没有 AI 服务**：识别降级为规则匹配，每条资源都要你确认；
- **没有 TMDB**：没有规范片名、海报和按类型地区的分类，识别结果也更容易需要确认。

监听用的是**你的个人账号**，只能读到这个账号本来就能看到的消息，所以要先在 Telegram 里关注频道，或者和 Bot 对话过。

## 2. 启动

### Docker（推荐）

需要 Docker Compose v2.24 及以上。不需要源码，只要一个 `compose.yaml`：

```bash
mkdir reelay && cd reelay
curl -fsSLO https://raw.githubusercontent.com/Derrors/reelay/main/compose.yaml
docker compose pull
# 镜像以 UID 10001 运行，先让数据卷可写
docker compose run --rm --no-deps --user root worker chown -R 10001:10001 /app/data
docker compose up -d web
```

管理台在 <http://127.0.0.1:28080>。默认拉取最新正式版；要固定版本，在同目录建一个 `.env` 写上 `REELAY_VERSION=0.3.0`。

### 本机运行

需要 Python 3.12 与 Node.js（构建管理台）：

```bash
git clone https://github.com/Derrors/reelay && cd reelay
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
(cd web && npm ci && npm run build)
python -m reelay.web
```

> [!TIP]
> 想先看看整条流程怎么走，可以运行 `python -m reelay.main --dry-run`：它用内存数据库和内置样例消息走完监听到入库的全过程，打印每条任务的处理轨迹，不需要任何账号，也不访问网络。

## 3. 填写设置

打开管理台，依次完成：

1. **设置 → 运行与安全**：设置登录密码。只在本机访问可以先跳过，要从其他设备访问时必须设置。
2. **设置 → Telegram**：填写 `api_id` / `api_hash`、控制 Bot Token 和你的数字 ID。
3. **设置 → AI 与 TMDB**：填写 AI 接口地址与密钥，从接口返回的列表里选择模型，点「测试连通性」确认能用；再填写 TMDB 密钥。
4. **设置 → 115 网盘**：用 115 App 扫码登录。

所有设置保存在 `data/` 目录：配置在 `data/config.yaml`，密钥单独存放在 `data/secrets.json`，页面上只显示「已设置」。

## 4. 登录 Telegram 并开始监听

登录个人账号需要输入验证码（开了两步验证还要输入密码），在交互终端里完成：

```bash
# Docker
docker compose run --rm --no-deps -it worker python -m reelay.login
docker compose up -d

# 本机运行（另开一个终端）
python -m reelay.login
python -m reelay.main
```

总览页的「后台 Worker」「Telegram 监听」「115 网盘」都显示正常，就说明已经在运行了。

## 5. 告诉它存到哪、听谁的

1. **设置 → 基础 → 保存目录**：新增目录别名（如「电影」「剧集」），点「选择 115 目录」从网盘里选中文件夹。别名与默认的一级分类同名时，资源会按「电影 / 国产电影」这样的二级目录自动归类。
2. **设置 → 监听来源 → 添加来源**：填写频道或 Bot 的 `@username`（或数字 ID），确认方式先选「每条都需我确认」。
3. **工具 → 识别测试**：贴一条这个来源的真实消息，看看识别结果、置信度和决策是否符合预期。

保存后不用重启，约 1 秒后开始监听新来源。

## 6. 处理第一条资源

来源发出新消息后：

1. 管理台的**待审批**出现一张资源卡片（配置了控制 Bot 的话，Telegram 里同时收到一张带按钮的卡片），点「转存」；
2. 任务依次经过 `queued → saving → stored → organizing → done`，115 的目标目录下出现按媒体库规范命名的文件夹和文件，Telegram 收到入库通知；
3. 有问题时，在**任务**里点开这条任务查看完整处理轨迹，或在**工具 → 运行日志**查看主进程日志。

## 接下来

- **少审批**：在待审批里对在追的剧点「追这部剧」，之后的新集自动转存；其余条目可以按建议批量处理。
- **写偏好**：在「设置 → 基础」用一句话描述想要什么、不要什么，AI 起草成过滤与分类规则，确认后生效。
- **放手自动**：确认得够多之后，「监听来源」和总览会提示哪些来源可以改为自动转存。
- **用手机或 Agent 操作**：给控制 Bot 发「待审批里有什么」，或用 MCP 让 Claude 帮你处理，见 [Agent 接入](agents.md)。
- **长期运行**：升级、备份与故障处理见 [部署与运维](deployment.md)；全部设置项见 [配置参考](configuration.md)。
