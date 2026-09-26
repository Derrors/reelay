# Docker 单机部署

镜像使用 Python 3.12 与固定版本的 115 SDK，前端在 Node 构建阶段编译，运行镜像只包含 Python 服务和静态资源。Compose 启动一个监听/Worker 进程和一个 Web 管理进程；两者共享本机 Docker 数据卷和配置目录。不要把 SQLite/WAL 与 Worker 文件锁迁移到网络文件系统，也不要同时启动另一个消费同一数据库的 Worker。

## 首次启动

先准备 `.env` 中的凭证与业务配置。以下命令会新建部署配置，请在已有部署时保留当前文件。

```bash
cp .env.example .env
mkdir -p deploy/config
cp config.example.yaml deploy/config/config.yaml
# 编辑 .env 和 deploy/config/config.yaml，填写凭证、来源与实际目录 CID。
docker compose build
# 镜像以 UID 10001 运行；让共享目录和数据卷可写。
docker compose run --rm --no-deps --user root worker chown -R 10001:10001 /app/config /app/data
# 首次在交互终端登录个人 Telegram 账号，验证码/密码直接交给 Telethon。
docker compose run --rm --no-deps -it worker python -m reelay.login
docker compose up -d
docker compose ps
```

打开 <http://127.0.0.1:28080>。Compose 默认只在宿主机回环地址开放 Web，当前没有内置登录。配置保存需要替换文件，因此挂载的是整个 `/app/config` 目录，而不是单个配置文件。

个人账号监听与控制 Bot 使用不同的 session 文件。Compose 固定把 session 和业务数据库放在 `/app/data`，把配置放在 `/app/config/config.yaml`，覆盖 `.env` 中的这些路径。控制 Bot 尚未配置时，审批与故障通知写控制台，任务仍可在 Web 审批；日志输出不是 Telegram 消息投递成功。

## 健康状态与恢复

Worker 的健康检查只读共享 SQLite，要求 Worker 心跳和消息源连接观察都在 30 秒内有效。Web 健康检查验证 API 服务自身，不代表 Worker 已在线或 115 凭证可用。管理台展示 Worker 心跳、实际采用的配置版本、消息源连接观察，以及持久化通知/事件队列的积压与失败。

配置校验失败时，运行进程继续使用上一个有效版本，并报告配置错误。有效配置通常在约 1 秒内被 Worker 采用，监听目标更新及来源切换还需最多一个轮询周期。长请求、系统调度和磁盘压力可能延长这个时间。环境凭证、控制 Bot 发送器和 Worker 并发数在启动时读取，修改后执行 `docker compose restart`。

SIGTERM 会触发协程取消和会话关闭；同步 SDK 请求仍等待线程结束，再释放 Worker 锁。Compose 给出 120 秒退出时间。超过退出时间被强制终止时，下一次启动按已持久化的转存/整理检查点恢复；转存结果未知只核验，不重发。

```bash
docker compose logs --tail=100 worker web
docker compose restart worker web
docker compose down
```

`down` 默认保留数据卷；不要使用 `down -v`，除非明确要删除业务状态和登录会话。备份或迁移前停止两个服务，再备份完整数据卷与部署配置，保证数据库和会话一致。

## 验证边界

当前提供部署文件和本地模拟验证。没有执行真实账号登录、实际 115 转存、长期断线测试或容器线上部署。Docker 镜像需要在有 Docker 的机器上实际构建和启动验收。

Telethon 启用自动重连和 session catch-up；尚未实现应用层按来源持久化历史水位的补拉、115 登录态续期与认证失效统一暂停、请求级令牌桶和自动撤销已转存文件。这些仍是常驻生产验收前的后续工作。
