# Docker 单机部署

镜像使用 Python 3.12 与固定版本的 115 SDK，前端在 Node 构建阶段编译，运行镜像只包含 Python 服务和静态资源。Compose 启动一个监听/Worker 进程和一个 Web 管理进程；两者共享同一个本机 Docker 数据卷 `/app/data`，数据库、配置、密钥、会话、115 凭证与日志都在里面。不要把 SQLite/WAL 与 Worker 文件锁迁移到网络文件系统，也不要同时启动另一个消费同一数据库的 Worker。

## 首次启动

配置与密钥都在管理台里填写，不需要事先编辑配置文件。

```bash
cp .env.example .env          # 只有 Web 监听地址等部署参数
mkdir -p deploy/plugins
docker compose build   # 国内网络可加 --build-arg NPM_REGISTRY=https://registry.npmmirror.com/ --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# 镜像以 UID 10001 运行；让数据卷可写。
docker compose run --rm --no-deps --user root worker chown -R 10001:10001 /app/data /app/config
docker compose up -d web
```

打开 <http://127.0.0.1:28080>：

1. 「设置 → 运行与安全」设置登录密码（要开放到局域网或公网时必须设置）；
2. 「设置 → Telegram」填写个人账号的 API ID / API Hash，以及控制 Bot 的 Token 与你的数字 ID；
3. 「设置 → AI 与 TMDB」填写 AI 接口与 TMDB key；「设置 → 115 网盘」扫码登录。

然后在交互终端登录个人 Telegram 账号（验证码/密码直接交给 Telethon），再启动 Worker：

```bash
docker compose run --rm --no-deps -it worker python -m reelay.login
docker compose up -d
docker compose ps
```

Compose 默认只在宿主机回环地址开放 Web。设置登录密码后，除登录、登录状态、健康检查和带签名的 STRM 跳转外，所有 `/api/*` 接口（含 SSE）都要求登录，同一客户端 5 分钟内连续 5 次密码错误会被拒绝尝试。

### 从旧版本升级

旧版本把配置放在 `deploy/config/config.yaml`、凭证写在 `.env`。保持 Compose 里 `./deploy/config:/app/config` 这一行和原来的 `.env` 不动，直接 `docker compose build && docker compose up -d`：首次启动时配置移进数据卷（原文件改名为 `config.yaml.migrated`），`.env` 里的设置和凭证导入管理台，日志会列出导入了哪些变量。确认管理台里的设置无误后，可以删掉 Compose 中的 `./deploy/config` 挂载和 `.env` 里除监听地址外的变量。详见 [配置参考](configuration.md#从旧版本升级)。

## 外部扩展

`deploy/plugins` 以只读方式挂载到 `/app/data/plugins`，放入的 `*.py` 文件或包目录在启动时被发现，按 `@register` 声明挂到 Provider/Hook/Event 扩展点，修改后 `docker compose restart` 即可，无需重建镜像。扩展以 Reelay 进程权限运行，只放入你信任的代码。导入失败不影响内置能力，错误显示在管理台「插件」页。

个人账号监听与控制 Bot 使用不同的 session 文件，和数据库、配置一样固定放在 `/app/data`。控制 Bot 尚未配置时，审批与故障通知写控制台，任务仍可在 Web 审批；日志输出不是 Telegram 消息投递成功。

## 健康状态与恢复

Worker 的健康检查只读共享 SQLite，要求 Worker 心跳和消息源连接观察都在 30 秒内有效。Web 健康检查验证 API 服务自身，不代表 Worker 已在线或 115 凭证可用；未登录时只返回存活状态，不含运行细节。

以下故障会通过控制 Bot 告警一次，恢复后发送恢复通知（告警与通知经持久化队列投递，进程重启不丢）：

- 115 cookie 缺失或失效：暂停转存与整理的领取（解析照常），任务回到原阶段等待，不消耗重试次数；在管理台重新扫码后 30 秒内自动继续。
- 消息源断开超过 `telegram.alert_after_sec`（默认 120 秒）或意外停止；重连后按每个来源已接收的最大消息 ID 补拉停机期间的消息（默认每个来源最多 200 条、7 天内）。管理台展示 Worker 心跳、实际采用的配置版本、消息源连接观察，以及持久化通知/事件队列的积压与失败。

配置校验失败时，运行进程继续使用上一个有效版本，并报告配置错误。有效配置通常在约 1 秒内被 Worker 采用，监听目标更新及来源切换还需最多一个轮询周期。长请求、系统调度和磁盘压力可能延长这个时间。Telegram 账号、控制 Bot、并发数与日志级别在主进程启动时读取，修改后执行 `docker compose restart worker`；AI 与 TMDB 的地址和密钥、115 限速等改了立即生效。

SIGTERM 会触发协程取消和会话关闭；同步 SDK 请求仍等待线程结束，再释放 Worker 锁。Compose 给出 120 秒退出时间。超过退出时间被强制终止时，下一次启动按已持久化的转存/整理检查点恢复；转存结果未知只核验，不重发。

```bash
docker compose logs --tail=100 worker web
docker compose restart worker web
docker compose down
```

`down` 默认保留数据卷；不要使用 `down -v`，除非明确要删除业务状态和登录会话。备份或迁移前停止两个服务，再备份完整数据卷，保证数据库和会话一致。

## STRM

在「插件」页启用 STRM 生成并配置输出目录（容器内路径，需另行挂载到宿主机供媒体服务器读取）与 Reelay 访问地址（媒体服务器能访问到的 Reelay Web 地址）。整理完成后生成 `.strm`，播放时请求 Reelay 的签名地址，由 Reelay 换取 115 直链并 302 跳转，视频流量不经过本机。Compose 默认只在宿主机回环地址开放 Web，媒体服务器在其他机器时需要调整 `ports`。

## 验证边界

真实账号已跑通监听、LLM 识别、人工审批、115 转存与整理（单源、剧集单集）；电影、多集合集、auto/default_pass 模式、多来源、长期断线与容器线上部署尚未实测。Docker 镜像需要在有 Docker 的机器上实际构建和启动验收。尚未实现自动撤销已转存文件。
