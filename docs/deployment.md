# 部署与运维

首次部署的步骤见 [快速开始](quickstart.md)。这里说明 Docker 部署长期运行时需要知道的事：怎么访问、怎么升级、怎么备份、出了问题怎么看。

## 运行方式

`compose.yaml` 启动两个容器，共用同一个数据卷 `/app/data`：

| 服务 | 进程 | 作用 |
| --- | --- | --- |
| `worker` | `python -m reelay.main` | 监听 Telegram、执行识别 / 转存 / 整理、控制 Bot、发送通知 |
| `web` | `python -m reelay.web` | 管理台与 API、Agent 用的 `/mcp`；只读写任务状态，不执行转存 |

数据库、配置、密钥、Telegram 会话、115 凭证与日志全部在数据卷里。同一个数据库只能有一个 `worker`（启动时用文件锁保证），也不要把数据卷放在网络文件系统上：SQLite 的 WAL 与文件锁在 NFS / SMB 上不可靠。

镜像发布在 `ghcr.io/derrors/reelay`，支持 linux/amd64 与 linux/arm64，标签有版本号（`0.3.0`）、次版本号（`0.3`）和 `latest`（最新正式版）。

## 访问与安全

Compose 默认只把管理台开放在宿主机的 `127.0.0.1:28080`。要从局域网或公网访问：

1. 先在「设置 → 运行与安全」设置登录密码；
2. 把 `compose.yaml` 里 `web` 的端口改为 `"28080:28080"`，或者经反向代理访问（建议配 HTTPS）；
3. `docker compose up -d web`。

设置密码后，除登录、登录状态和健康检查外，所有 `/api/*` 接口（含实时推送）都要求登录；同一客户端 5 分钟内连续 5 次密码错误会被暂时拒绝。登录会话有效期 7 天，Web 重启不影响；修改密码后其他会话全部失效。

Agent 经 `/mcp` 接入时用的是独立的 MCP 访问令牌，与登录密码无关，见 [Agent 接入](agents.md)。

## 版本、升级与回滚

升级到 `.env` 里 `REELAY_VERSION` 指定的版本（不写时为 `latest`）：

```bash
docker compose pull
docker compose up -d
```

升级前先看 [GitHub Release](https://github.com/Derrors/reelay/releases) 或 [CHANGELOG](../CHANGELOG.md)。数据库结构会随版本升级，结构升级后旧版本程序会拒绝打开它，所以：

- 升级前先 [备份](#备份与迁移) 数据卷；
- 要回滚时，把 `REELAY_VERSION` 改回原来的版本，并连同数据卷一起恢复到升级前的备份。

## 从源码构建

需要改代码，或网络拉不到 GHCR 时，在仓库目录用 `compose.build.yaml` 覆盖镜像、在本机构建：

```bash
docker compose -f compose.yaml -f compose.build.yaml build \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com/ \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple   # 国内网络可加这两个镜像源
docker compose -f compose.yaml -f compose.build.yaml up -d
```

之后的 `run`、`restart`、`logs` 等命令也要带上这两个 `-f`，否则 Compose 会改用发布的镜像。

## 备份与迁移

所有状态都在数据卷 `reelay-data` 里。备份前先停止两个服务，保证数据库与会话一致：

```bash
docker compose stop
docker run --rm -v reelay_reelay-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/reelay-data-$(date +%Y%m%d).tgz -C /data .
docker compose start
```

数据卷的实际名称是「项目目录名_reelay-data」，可以用 `docker volume ls` 确认。迁移到新机器时，在新机器上恢复同一个数据卷再启动即可；不要用 `docker compose down -v`，它会删除数据卷。

## 外部扩展

把扩展（`*.py` 文件或包目录）放进 `compose.yaml` 同目录的 `deploy/plugins/`，它以只读方式挂载到 `/app/data/plugins`，`docker compose restart` 后生效，不用重建镜像。扩展以 Reelay 进程的权限运行，只放你信任的代码；导入失败不影响内置能力，错误显示在管理台「插件」页。写法见 [插件开发](plugins.md)。

## 健康检查与故障恢复

两个容器都带健康检查：

- **worker**：要求主进程心跳和 Telegram 连接在 30 秒内有效；
- **web**：只检查 API 自身，不代表主进程在线或 115 凭证可用。

管理台总览显示主进程心跳、正在使用的配置版本、各项连接状态，以及通知队列的积压与失败。以下故障会通过控制 Bot 告警一次，恢复后再通知（告警经持久化队列投递，重启不丢）：

| 故障 | Reelay 的处理 |
| --- | --- |
| 115 登录失效 | 暂停转存与整理（识别照常），任务原地等待、不消耗重试次数；在管理台重新扫码后 30 秒内自动继续 |
| Telegram 断开超过 `telegram.alert_after_sec`（默认 120 秒） | 告警；重连后按每个来源已收到的最大消息 ID 补拉停机期间的消息（默认每个来源最多 200 条、7 天内） |
| 转存后 115 迟迟没有落地 | 任务转「待人工」，后台自动只读复查（5 分钟起，间隔加倍，24 小时内），通过后照常整理 |
| 配置文件写错 | 继续使用上一个有效版本，总览显示错误 |

**设置何时生效**：多数设置保存后约 1 秒生效；Telegram 账号、控制 Bot、并发数与日志级别在主进程启动时读取，改了之后执行 `docker compose restart worker`。

**停止与重启**：停止时会等待进行中的请求结束，Compose 留出 120 秒。被强制终止也没关系，下次启动从已保存的检查点继续；转存结果不明的任务只核验，不会重发。

## 常用命令

```bash
docker compose ps                          # 状态与健康检查
docker compose logs --tail=100 worker web  # 最近日志（也可以在管理台「工具 → 运行日志」查看）
docker compose restart worker              # 改了需要重启的设置后
docker compose run --rm --no-deps -it worker python -m reelay.login   # 重新登录 Telegram
docker compose down                        # 停止并删除容器，数据卷保留
```
