# Quick Start

[Documentation home](README.md) · [User guide](user-guide.md) · [FAQ](faq.md)

This guide takes you from a fresh setup through **starting the console → configuring accounts → logging in to Telegram → adding a source → verifying your first item in the library**. Start by manually approving one item, check the result, then enable subscription-based approval or automatic processing.

> [!IMPORTANT]
> Only process content you have the right to access and save. Read the [disclaimer](../../DISCLAIMER.md) before use. Leave automatic version upgrades and overwriting disabled at first, so configuration mistakes do not affect existing files.

## Before you start

| Requirement | Purpose | Where to get it |
| --- | --- | --- |
| A machine that can keep running | Hosts Reelay; Docker supports amd64 / arm64 | A NAS, Linux server, or similar; Windows requires Docker |
| Docker and Compose v2.24+ | Run the console and background service | Check with `docker --version` and `docker compose version` |
| A personal Telegram account | Reads channels and groups you have joined and Bots you have contacted | Your own account |
| Telegram API ID / API Hash | Lets the application log in as your personal account | <https://my.telegram.org> → API development tools |
| A 115 account | Stores transferred files and cloud downloads | Authorize later in the console by scanning a QR code with the 115 app |
| Control Bot Token (recommended) | Receives approval cards and notifications, and handles commands | Create a Bot through @BotFather in Telegram |
| Your numeric Telegram ID (required for the control Bot) | Restricts control Bot access to you | For example, ask @userinfobot; enter the numeric ID, not an `@username` |
| An OpenAI-compatible AI endpoint (recommended) | AI identification, rule generation, conversation, and more | A provider of your choice or a compatible local model |
| TMDB credentials (recommended) | Confirms title identity, standardizes names, provides posters, and matches subscriptions | <https://www.themoviedb.org/settings/api> |

**Your personal account and the control Bot are different:**

- **The personal account reads messages**: it can only read sources that account already has access to. Creating a control Bot does not automatically give Reelay access to every channel.
- **The control Bot interacts with you**: it sends cards and notifications and receives approval and query commands. Open the Bot you created and send `/start` first.

You can approve items in the console without a control Bot. If you do not use AI, select “Rule-based identification” (「规则识别」) for a source. The default AI identification falls back when its endpoint is unavailable and forces manual confirmation. Without TMDB, identity checks, classification, and automatic approval are affected; **configure TMDB if you need subscription matching**.

The management console and Bot UI are currently in Chinese. The instructions below explain their controls in English and retain the Chinese labels you will see on screen.

## 1. Start the services

### Docker (recommended)

```bash
mkdir reelay && cd reelay
curl -fsSLO https://raw.githubusercontent.com/Derrors/reelay/main/compose.yaml
docker compose pull
docker compose up -d web
```

These commands start only the **web management console**; Telegram monitoring has not started yet. Run all subsequent `docker compose` commands from this directory.

The latest stable image is used by default. To pin a version, set `REELAY_VERSION=<版本号>` in a `.env` file in the same directory, replacing `<版本号>` with a version from [Releases](https://github.com/Derrors/reelay/releases). Keep only deployment parameters in `.env`; enter accounts and secrets in the console later.

**Success check**: `docker compose ps` shows `web` running, and its health status becomes `healthy` shortly afterward.

### Run locally

Without Docker, you need Python 3.12+, Node.js (Node.js 24 is recommended to match the project's Docker build), and npm.

```bash
git clone https://github.com/Derrors/reelay && cd reelay
python3.12 -m venv .venv
source .venv/bin/activate
pip install -c constraints.txt -e ".[all]"
npm ci --prefix web
npm run build --prefix web
python -m reelay.web
```

Keep this terminal running. Later, to log in to Telegram and run the background service, open another terminal, enter the same repository directory, and run `source .venv/bin/activate`.

> [!TIP]
> To try the workflow without configuring real accounts, run `python -m reelay.main --dry-run`. It uses built-in examples and an in-memory cloud drive, does not read local configuration or secrets, and makes no network requests. A successful dry run does not mean your real accounts are connected.

## 2. Open the console

### Open the console

**If your browser is on the deployment machine**: open <http://127.0.0.1:28080>.

**If Reelay runs on a NAS or another machine on your LAN**: by default, only the deployment machine itself can access it. In `compose.yaml`, change this line under `web.ports`:

```yaml
- "127.0.0.1:28080:28080"
```

To:

```yaml
- "28080:28080"
```

Then run `docker compose up -d web` and open `http://NAS的局域网IP:28080`, replacing `NAS的局域网IP` with the NAS's LAN IP address. Use a firewall to restrict access to trusted networks. For a local installation, run `REELAY_WEB_HOST=0.0.0.0 python -m reelay.web` to listen on the LAN, and likewise restrict who can connect.

**If Reelay runs on a remote public server**: prefer an SSH tunnel or controlled private-network access. Do not expose the management port and leave initial setup open to arbitrary internet visitors. For HTTPS, reverse proxies, and trusted addresses, see [Deployment and operations](deployment.md#access-and-security).

### Set the console password

On your first visit, set a login password of at least 8 characters. Initial setup is allowed only from the local machine or LAN, using an IP address or localhost. **You cannot complete initial setup through a domain name.** Use an IP address or a secure tunnel first, then configure domain-based access.

**Success check**: after setting the password, you can enter the console and open “Settings” (「设置」).

> [!NOTE]
> Docker uses a named data volume by default, with no manual permission changes needed. To use a `./data` directory on your NAS, follow [the host-directory instructions](deployment.md#storing-data-in-a-host-directory) to configure the user, `HOME`, and mounts for both services. Do not make the entire data directory writable by everyone.

## 3. Configure accounts and networking

Configure these in the order below, saving each page when finished.

### Network proxy (optional)

Under “Settings → Network proxy” (「设置 → 网络代理」), enter an HTTP or SOCKS5 proxy, choose which of Telegram, TMDB, AI, and GitHub (update checks) should use it, then click “Test proxy” (「测试代理」). 115 always connects directly.

Inside a Docker container, `127.0.0.1` refers to that container. If the proxy runs on the host or another device, enter a LAN address reachable from the container instead of copying the host's `127.0.0.1` address.

### Telegram

Enter:

- API ID and API Hash: required to log in to your personal account.
- Control Bot Token: optional, but recommended.
- Your numeric ID: the sole authorized operator of the control Bot.

This saves the connection details; **you must still log in to your personal account in a terminal in the next step**.

### AI and TMDB

- AI: enter the compatible endpoint and key, then choose a model from the model list. Leave reasoning effort empty if you are unsure. Click “Test connection” (「测试连通性」), then save after it succeeds.
- TMDB: enter an API Key or Read Access Token and confirm connectivity.

Testing AI contacts your chosen service and may consume a small number of tokens. If your network cannot reach TMDB, configure a proxy or choose a reachable address as described in the [configuration reference](configuration.md#ai-services-and-tmdb-llm-tmdb).

### 115 cloud drive

Open “Settings → 115 cloud drive” (「设置 → 115 网盘」), scan the authorization QR code with your own 115 app, and wait for the page to show that you are logged in.

**Success check**: 115 credentials show as ready, and connection tests pass for any configured AI / TMDB services. Secret fields only show whether a value has been set; the page does not return the original secret text.

## 4. Log in to your personal account and start monitoring

### Docker

```bash
docker compose run --rm --no-deps -it worker python -m reelay.login
```

Follow the prompts to enter your personal account's phone number and verification code. If Telegram two-step verification is enabled, also enter your password. Once login is complete, start both services:

```bash
docker compose up -d
```

### Run locally

In another terminal with the virtual environment activated, run:

```bash
python -m reelay.login
python -m reelay.main
```

The main background process must keep running; opening the console alone is not enough.

**Success check**: “Runtime status” (「运行状态」) on the overview shows the Worker online, Telegram connected, and 115 credentials ready. The Worker may show `unhealthy` before configuration or login is complete. Do not repeatedly recreate containers based on that status alone; finish the account setup first.

## 5. Set save directories

Open “Settings → General → Save directories” (「设置 → 基础 → 保存目录」):

1. Add aliases, for example 「电影」 (movies), 「剧集」 (series), and 「默认」 (default).
2. Click “Choose 115 directory” (「选择 115 目录」) and select a destination folder on the drive for each alias.
3. Under “Default processing mode” (「默认处理方式」), select the default save directory and use “Manual confirmation” (「人工确认」) to begin with.
4. Save.

**An alias is a display name in Reelay, not a local disk path to type in.** The default organizer plugin also creates category and title subdirectories under the destination. See [User guide: save directories and organization](user-guide.md#save-directories-and-organization) for how the path is chosen.

Start with a separate test directory, then use your real media library only after the organization result meets your expectations.

## 6. Add a source to monitor

### First check that your personal account can access it

In Telegram, join the channel or group, or start a conversation with the Bot. Reelay cannot bypass the account's access permissions.

| Source | What to enter |
| --- | --- |
| Bot, channel, or group with a username | `@username`, for example `@example_channel` |
| Private channel or group without a username | A numeric ID beginning with `-100`. If a message link is `https://t.me/c/1234567890/42`, enter `-1001234567890` as the source |

### Add manually to understand the settings

Under “Settings → Sources” (「设置 → 监听来源」), click “Add source” (「添加来源」) and fill in:

| Setting | Recommended starting value | Why |
| --- | --- | --- |
| Bot or channel (「Bot 或频道」) | Add just one source | Makes configuration problems easier to spot; keep differently formatted sources separate at first |
| Confirmation mode (「确认方式」) | Manual confirmation (「人工确认」) | Check identification and directories before deciding to transfer |
| Identification method (「识别方式」) | AI identification (「AI 识别」) if AI is configured; otherwise rule-based identification (「规则识别」) | Avoids triggering fallback on every message when AI is absent |
| Approval mode (「放行方式」) | Rule-based decisions (「规则判断」) | Ensures you review each item at first; “Rules + subscription approval” (「规则 + 订阅放行」) may automatically approve new subscribed episodes |
| Subscriptions only (「仅处理订阅」, since v1.5.0) | Off initially | Enabling it without subscriptions or a TMDB match directly ignores the content |
| Bot notifications (「Bot 通知」) | On if the control Bot is configured | Sends cards in Telegram; you can still approve in the console when it is off |
| Save to (「保存到」) | The test directory you just created | Verify the setup without directly affecting your existing library |

You do not need to change every setting. Unset source fields inherit global defaults; boolean switches can be explicitly turned off to override a globally enabled setting.

### Set up from sample messages (optional)

If AI is configured, you can also click “Set up from sample messages” (「用样例消息接入」), enter the source, and paste a few representative messages. Let AI draft rules, inspect each test result, then add the source to the list and save. Include ordinary resources as well as ads or chat, rather than testing only examples expected to succeed.

An AI draft does not establish that the rules are correct. You must still check identification and filtering results.

### Run “Test identification” first

Paste a sample message containing content you are entitled to save, then check:

- Whether the title, year, season, and episodes are correct.
- Whether extracted 115 or ed2k links are shown.
- Why the item would be transferred, sent for approval, or ignored. Read the processing trace if anything is unclear.

“Test identification” (「测试识别」) uses the settings currently on the card, **including unsaved changes**. It creates no task and transfers nothing, but AI, TMDB, and share probing may contact their respective services.

Save the source settings once the result looks right.

**Success check**: the source appears in the list with no unsaved-changes warning. A new source starts monitoring from the time it is added; earlier message history is not automatically imported.

## 7. Verify your first item in the library

Wait for a new message from this source containing content you are entitled to save:

1. A record appears under “Tasks” (「任务」). If it requires manual confirmation, it appears under “Pending approval” (「待审批」).
2. Open the card and check the title, season and episodes, edition, and actual destination path. Correct identification mistakes first.
3. Click transfer (「转存」). The task is queued, then transferred, verified, and organized.
4. Confirm that the task finally shows “Added to library” (「已入库」), and inspect the folders, filenames, and file count in 115.
5. If you use Jellyfin / Emby, access these files through the mounting or media-access setup you already use.

**An item not appearing under pending approval is not necessarily a failure**: it may have been filtered out, already be in the library, or qualify for automatic approval. Check the outcome and reason under “Tasks” instead of relying only on whether Telegram shows a card.

If a transfer gets stuck, follow the [FAQ](faq.md). Do not repeatedly create the same task.

## What to configure next

- **Automatically collect new episodes you follow**: select “Rules + subscription approval” (「规则 + 订阅放行」) and add titles and seasons under “Series subscriptions” (「追剧订阅」).
- **Receive only subscribed content**: use v1.5.0 or later, enable “Subscriptions only” (「仅处理订阅」), and make sure TMDB is available. See the [user guide](user-guide.md#subscriptions-only) for the boundaries.
- **Reduce identification mistakes**: adjust source rules, test both valid resources and messages that should be ignored, then save.
- **Organize automatically**: preview directories and naming templates on the plugins page. Understand conflict policies before enabling automatic version upgrades.
- **Run long term**: back up data before upgrading. See [Deployment and operations](deployment.md).

## Recommended source

If you are unsure where to start, the maintainer uses **[@QukanMovie](https://t.me/QukanMovie)**. It is a third-party public channel, not part of Reelay. You are responsible for deciding whether to use it and which content to transfer.

1. Join the channel with the personal account used for monitoring, then add `@QukanMovie` as a source.
2. Under “Message must contain” (「消息必须包含」), enter `115.com`, `115.cn`, `115cdn.com`, and `ed2k://`, one per line. Matching any one is enough. Entering only `115` blocks messages that contain only ed2k links.
3. Under “Ignore if these words appear” (「包含这些词直接忽略」), you can enter `广告` (advertisement) and `招商` (business solicitation).
4. Verify the setup with “Manual confirmation” (「人工确认」) first. Select “Rules + subscription approval” (「规则 + 订阅放行」) later if you want new subscribed episodes approved automatically.

> [!NOTE]
> Reelay does not guarantee the content, legality, or availability of third-party channels and does not provide media resources. Follow local laws and copyright requirements. See the [disclaimer](../../DISCLAIMER.md).

---

Next: [Everyday user guide](user-guide.md) · Need help? [FAQ](faq.md) · Looking up a setting? [Configuration reference](configuration.md)
