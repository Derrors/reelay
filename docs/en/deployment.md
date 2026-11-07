# Deployment and Operations

[Documentation home](README.md) · [Quick start](quickstart.md) · [FAQ](faq.md)

For your first deployment, see the [Quick start](quickstart.md). This page covers what you need to know to keep a Docker deployment running: access, upgrades, backups, and troubleshooting.

## How It Runs

`compose.yaml` starts two containers that share the same data volume at `/app/data`:

| Service | Process | Role |
| --- | --- | --- |
| `worker` | `python -m reelay.main` | Monitor Telegram; identify, save, and organize resources; run the control Bot; send notifications |
| `web` | `python -m reelay.web` | Serve the web console, API, and `/mcp` for agents; read and write task state only, without performing transfers |

The database, configuration, secrets, Telegram sessions, 115 credentials, and logs all live in the data volume. The image runs as UID 10001. A new named volume inherits ownership from the image, so no manual permission changes are needed. To store data in a host directory, see [Storing Data in a Host Directory](#storing-data-in-a-host-directory). Only one `worker` may use a given database, enforced by a file lock at startup. Do not put the data volume on a network filesystem: SQLite WAL and file locking are unreliable on NFS / SMB.

Images are published at `ghcr.io/derrors/reelay` for linux/amd64 and linux/arm64. Tags include full versions such as `1.0.0`, minor versions such as `1.0`, and `latest` for the latest stable release.

### Storing Data in a Host Directory

On a NAS, storing data in a host directory, such as `./data` beside the Compose file, makes it easier to inspect and back up. There are two options:

**Run as your host user (recommended)**: your user owns the files, so you can read and write them directly on the host. Add `user`, `HOME`, and the mount to both the `worker` and `web` services:

```yaml
services:
  worker:
    user: "1000:1000"      # Your UID:GID; check with the id command
    environment:
      HOME: /app/data      # Required; see below
    volumes:
      - ./data:/app/data
  web:
    user: "1000:1000"
    environment:
      HOME: /app/data      # Add to web's existing environment section
    volumes:
      - ./data:/app/data
```

- `HOME` is required: the image has no home directory for this user, and the 115 client creates a cache in the home directory at startup. Without it, the client cannot load, so neither transfers nor organization will work. Pointing it to the data directory also keeps the cache with your data.
- Create the directory as this user first (`mkdir data`); otherwise Docker creates it as root and the container cannot write to it.
- Put external plugins directly in `data/plugins/`; you do not need a separate `deploy/plugins` mount.

**Keep the image's UID 10001**: change only the mount to `./data:/app/data`, and first run `sudo chown -R 10001:10001 data`. You will then need root privileges to read these files on the host.

## Access and Security

By default, Compose exposes the web console only on the host's `127.0.0.1:28080`. To access it from your LAN or the internet, change the `web` port mapping in `compose.yaml` to `"28080:28080"`, or use a reverse proxy, preferably with HTTPS. Then run `docker compose up -d web`.

**Set a login password the first time you open the web console** (at least 8 characters). You will enter the console immediately afterward. A NAS often has no browser, so a device on the LAN can also complete this step, but only when the request comes from the LAN or the local machine and the address bar contains an IP address or localhost. Requests from the public internet are not allowed, nor are requests using a domain name. This prevents a page using DNS rebinding to impersonate a local host from taking over initial setup. If you normally access the console through a reverse proxy and domain name, open it by IP once to set the password. Until a password is set, endpoints other than initial setup accept requests only from local addresses.

Once a password is set, only login, logout, login status, initial setup, and the health check (`/api/health`, which reports only whether the service is online) are accessible anonymously. All other `/api/*` endpoints, including live updates and API documentation (`/api/docs`, `/api/openapi.json`), require login. Sessions last 7 days and survive Web process restarts. Changing the password invalidates all other sessions. Over HTTPS the session cookie is marked `Secure` (a reverse proxy must forward `X-Forwarded-Proto` and be trusted as described below). Write operations accept requests only from the console's own origin; writes originating from other ports on the same machine or from other websites are rejected.

Failed logins are counted by client IP. Five consecutive incorrect passwords from the same IP within 5 minutes trigger a temporary block. Behind a reverse proxy, Reelay sees the proxy's address unless you list it in `REELAY_FORWARDED_ALLOW_IPS` in `.env` (in a Docker network this is often the gateway, such as `172.18.0.1`). This allows counting by the real client address forwarded by the proxy. Otherwise, everyone shares one counter, and someone else's mistakes can temporarily lock you out too.

Agents connecting through `/mcp` use separate MCP access tokens, not the login password. See [Agent access](agents.md).

## Versions, Upgrades, and Rollbacks

Upgrade to the version specified by `REELAY_VERSION` in `.env`, or `latest` if it is unset:

```bash
docker compose pull
docker compose up -d
```

When a new version is available, the console shows a notice once in the top-right corner (once per version; it disappears after a few seconds) and keeps an entry at the bottom of the sidebar. Open the “版本更新” (Updates) page to see the release notes and how to upgrade. The control Bot also announces it once (plugin “版本更新检查” (Update check), which can be disabled on the “插件” (Plugins) page). Docker deployments are only notified; run the commands above on the server to upgrade.

When running from a git clone without Docker, click “立即升级” (Upgrade now) on the “版本更新” (Updates) page and confirm. In the background Reelay fetches the new release tag and fast-forwards the code, runs `pip install -c constraints.txt -e ".[all]"` if `pyproject.toml` or `constraints.txt` changed, rebuilds the console if it changed, and then restarts the main process and the web service in place; the page reloads automatically. Command output goes to `data/upgrade.log`. Requirements: the web service was started with `python -m reelay.web` (or `reelay-web`), tracked files have no local changes, and the current commit precedes the new release (no commits of your own); npm is also needed when the console changed. If installing dependencies or building fails, the code is restored to the previous version and nothing restarts.

Before upgrading, read the [GitHub Release](https://github.com/Derrors/reelay/releases) or [CHANGELOG](../../CHANGELOG.md). Releases may upgrade the database schema, and older versions refuse to open a newer schema. Therefore:

- [Back up](#backups-and-migration) the data volume before upgrading.
- To roll back, set `REELAY_VERSION` to the previous version and restore the data volume from the pre-upgrade backup as well.

To roll back a source deployment, stop the main process and the management service, restore the pre-upgrade backup of `data/`, then switch back to the previous tag and restart:

```bash
git checkout v<version>
pip install -c constraints.txt -e ".[all]"
cd web && npm ci && npm run build
```

## Building from Source

If you need to change the code or cannot pull from GHCR, use `compose.build.yaml` in the repository directory to override the images and build locally:

```bash
docker compose -f compose.yaml -f compose.build.yaml build \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com/ \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple   # Optional mirrors for networks in China
docker compose -f compose.yaml -f compose.build.yaml up -d
```

Include both `-f` options in subsequent `run`, `restart`, `logs`, and similar commands too, or Compose will use the published images instead.

## Backups and Migration

All state lives in the `reelay-data` volume. Stop both services before backing up to keep the database and sessions consistent:

```bash
docker compose stop
docker run --rm -v reelay_reelay-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/reelay-data-$(date +%Y%m%d).tgz -C /data .
docker compose start
```

The actual volume name is `<project-directory-name>_reelay-data`; check it with `docker volume ls`. If you [store data in a host directory](#storing-data-in-a-host-directory), stop the services and archive that directory directly. Do not use `docker compose down -v`; it deletes the data volume.

To restore a backup (this erases the volume's current contents), run the commands below. The same steps migrate to a new machine; run `docker compose up --no-start` there first to create the volume:

```bash
docker compose stop
docker run --rm -v reelay_reelay-data:/data -v "$PWD":/backup alpine \
  sh -c 'find /data -mindepth 1 -delete && tar xzf /backup/reelay-data-<date>.tgz -C /data'
docker compose start
```

## Local Evaluation

Before changing the identification prompt or the model, you can compare results against your own manual decisions. Samples contain real message text and stay in `data/eval/`. Evaluation calls your configured AI endpoint and may incur charges.

```bash
python -m reelay.eval export                 # Export samples to data/eval/samples.jsonl (optional)
python -m reelay.eval run --limit 50         # Evaluate the latest 50 with the current prompt and model; reports go to data/eval/report-*.json
python -m reelay.eval run --model gpt-4o     # Evaluate another model; --dataset uses exported samples, --examples sets the example count
python -m reelay.eval compare old-report.json new-report.json
```

For Docker, prefix the commands with `docker compose run --rm --no-deps worker`.

## External Extensions

Put extensions, either plugin directories or individual `*.py` files, in `deploy/plugins/` beside `compose.yaml`. This directory is mounted read-only at `/app/data/plugins`. Run `docker compose restart` to load them; no image rebuild is needed. Extensions run with the Reelay process's privileges, so use only code you trust. Import failures do not affect built-in functionality; errors appear on the web console's “插件” (Plugins) page. See [Plugin development](plugins.md) for implementation details.

## Health Checks and Recovery

Both containers have health checks:

- **worker**: requires a valid main-process heartbeat and Telegram connection within the last 30 seconds. Before Telegram is configured or logged in, the main process still runs normally, including identification, transfers, and the control Bot, but its health status is unhealthy. It recovers automatically after login.
- **web**: checks only the API itself, not whether the main process is online or 115 credentials work.

“运行状态” (Runtime status) on the console overview shows the main-process heartbeat, active configuration version, connection status, and notification-queue backlog and failures. The current version appears at the bottom of the sidebar; include it when reporting a problem. The following failures trigger a one-time alert through the control Bot, followed by a recovery notification. Alerts use a durable queue and survive restarts. Alerts that could not be sent while Telegram was disconnected are delivered after reconnection with the time the failure occurred.

| Failure | How Reelay handles it |
| --- | --- |
| 115 login expires | Pause transfers and organization while identification continues. Tasks wait at their current stage without consuming retries; they resume automatically within 30 seconds of scanning the QR code again in the web console |
| 115 rate or abuse protection, such as access limits or excessive operations | Pause all 115 requests for `p115.risk_cooldown_sec` (1 hour by default), including transfers and organization. Tasks resume when the cooldown ends, without being counted as failures. The cooldown survives main-process restarts |
| Telegram is disconnected, or not yet configured or logged in, for longer than `telegram.alert_after_sec` (120 seconds by default) | Send an alert, then include the outage duration in the recovery notification. After a connection reset or lack of response, the main process reconnects itself and retries every 30 seconds if necessary; no restart is needed. If Telegram is not configured or logged in, it checks again every 30 seconds and connects automatically once ready. When monitoring starts or reconnects, it backfills messages missed during downtime or disconnection using the highest received message ID for each source, by default up to 200 messages per source from the last 7 days |
| Files take too long to arrive in 115 after a transfer | Move the task to “待人工” (Needs manual intervention), then perform automatic read-only checks in the background, starting after 5 minutes with doubling intervals for up to 24 hours. Organization proceeds normally once verification passes. Checks are normally silent; if 3 consecutive results are unchanged, such as no files having arrived, send an early reminder roughly half an hour after the transfer while checks continue. If verification still has not passed after 24 hours and no reminder was sent earlier, notify then |
| Invalid configuration file | Keep using the last valid version. The overview and settings pages show the faulty file and reason; saving through the console is blocked until the file is fixed |

**When settings take effect**: most settings apply about 1 second after saving. Control Bot settings, concurrency, and log level are read when the main process starts; Telegram account settings are read when the listener connects. After changing these, run `docker compose restart worker`.

**Stopping and restarting**: shutdown waits for in-flight requests to finish; Compose allows 120 seconds. Even after a forced termination, the next startup resumes from saved checkpoints. Tasks with uncertain transfer outcomes are verified only, never resent.

## Common Commands

```bash
docker compose ps                          # Status and health checks
docker compose logs --tail=100 worker web  # Recent logs (also under “工具 → 运行日志” in the console sidebar)
docker compose restart worker              # After changing settings that require a restart
docker compose run --rm --no-deps -it worker python -m reelay.login   # Log in to Telegram again
docker compose down                        # Stop and remove containers; keep the data volume
```
