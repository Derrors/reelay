# Frequently Asked Questions

[Documentation home](README.md) · [Quick start](quickstart.md) · [User guide](user-guide.md)

First locate the stage where the problem occurs: **console access → background connections → receiving messages → identification and approval → transfer → organization**. Do not immediately recreate containers or clear data; doing so may lose login sessions, history, and transfer checkpoints.

## Question index

- [Installation and access](#installation-and-access)
- [Messages and sources](#messages-and-sources)
- [Identification, approval, and subscriptions](#identification-approval-and-subscriptions)
- [115 transfers and organization](#115-transfers-and-organization)
- [Costs, privacy, and data](#costs-privacy-and-data)
- [What to include in a bug report](#what-to-include-in-a-bug-report)

## Installation and access

### 1. Docker is running. Why can I not open the console from another computer?

The default port mapping is `127.0.0.1:28080:28080`, which listens only on the deployment machine itself.

- If Reelay runs on a NAS, `127.0.0.1` in your computer's browser does not refer to the NAS.
- For LAN access, change the port binding, open the NAS's LAN IP address, and check the firewall.
- For a public server, prefer a secure tunnel or controlled reverse proxy. Directly exposing the management port is not recommended.

See [Open the console](quickstart.md#open-the-console) for instructions.

### 2. Why does initial setup not work through a domain name, or why am I unable to set a password?

Initial setup is restricted to the local machine or LAN, and the address bar must use an IP address or localhost. This helps prevent another website from hijacking initialization through domain-based access.

Set the initial password through a LAN IP address or secure tunnel first, then configure a domain name and HTTPS. Do not disable authentication to solve this.

### 3. Why does the Worker show unhealthy when the console works?

`web` and `worker` are separate processes. A healthy web service only means the management API can answer requests; it does not mean Telegram is connected or the background service can transfer files.

Check these in order:

1. Did you start only `web` without running `docker compose up -d` afterward?
2. Have you entered the Telegram API ID / API Hash and logged in to your personal account in a terminal?
3. Does the overview report not logged in, connection failed, or a missing Worker heartbeat?
4. What specific reason appears in `docker compose logs --tail=100 worker`?

The Worker can run while failing its health check if Telegram configuration or login is incomplete. It will usually connect automatically once the missing setup is completed.

### 4. What should I do about Permission denied in the data directory?

The default named volume needs no extra permission changes. When using a host directory, make sure `web` and `worker` see the same data and run as users with access. With a custom UID, `HOME` must also point to a writable data directory.

Follow [the host-directory deployment instructions](deployment.md#storing-data-in-a-host-directory). Do not use `chmod -R 777` as a general fix: the data directory contains login credentials.

## Messages and sources

### 5. Why did I not receive old messages after adding a source?

A new source receives messages from the time it is added; earlier history is not automatically imported. Catch-up for an existing source after a disconnection uses the saved receive position and is limited by both message count and age. These are two different situations.

Use “Test identification” (「测试识别」) to verify the format first, but remember that a test does not create real tasks.

### 6. A channel posted a new message. Why did Reelay do nothing?

Check these in order:

1. Has the logged-in personal account joined the channel or group, or started a conversation with the Bot?
2. Was the source saved successfully, with the correct `@username` or numeric ID?
3. Is the Worker online and Telegram connected?
4. Look under “Tasks” (「任务」): the message may have arrived but been ignored because of keywords, duplicates, or subscription filtering.
5. Read the task trace. If there is no record, check runtime logs and monitoring status.

The control Bot receiving your commands does not prove that the personal account used for monitoring is logged in successfully.

### 7. Does a missing Telegram approval card mean processing failed?

Not necessarily. Possible reasons include:

- No control Bot is configured, the owner's numeric ID is wrong, or you have not sent `/start` to your own Bot yet.
- Bot notifications are disabled for the source.
- The item was automatically approved or ignored, so it is not awaiting approval.
- It is a historical message fetched during disconnection catch-up, which does not send individual approval cards.
- Sending the notification failed temporarily; check the overview or logs.

Check the task state in the console first. **Resending a notification only sends the message again; it does not transfer the files again.**

### 8. “Test identification” succeeded. Why are there no files in my drive?

A test only previews parsing, confidence, and the decision. It does not create a task or transfer files. “Would transfer” in a preview is a prediction under the current conditions, not a completion notification.

For a real transfer, a new source message must enter the actual task workflow, pass approval or automatic-approval checks, and complete transfer verification and organization.

## Identification, approval, and subscriptions

### 9. Why was a message ignored even though it contains an ed2k link?

Read the task trace rather than checking only for a link:

- If “Message must contain” (「消息必须包含」) lists only `115` or 115 domains, it blocks ed2k-only messages. Allow `ed2k://` too.
- Other keyword, type, resolution, or subscription conditions may not have passed.
- Extracting a link, meeting your collection criteria, and 115 being able to cloud-download it are three different things.

Older test interfaces may incorrectly label ed2k-only results as “No 115 link found” (「没有找到 115 链接」). This display issue is fixed in the current source; check the [changelog](../../CHANGELOG.md) for release availability. Do not fabricate a 115 link just to turn a badge green.

### 10. Why is an item awaiting approval even though I selected “AI processing”?

“AI processing” (「AI 处理」) is not unconditional approval. Manual confirmation may still be required because of low confidence, an unreliable TMDB match, AI parsing fallback, unclear filter information, or a share-check problem.

The processing trace records the reason. Resolve missing information or service failures first; simply lowering the confidence threshold to bypass checks is not recommended.

### 11. Why were subscribed episodes transferred automatically despite “Manual confirmation”?

With “Rules + subscription approval” (「规则 + 订阅放行」) or “AI preference decisions” (「AI 按偏好判断」) as the approval mode, eligible subscribed episodes may be promoted from manual confirmation to automatic approval.

If you want to **confirm every item**, use “Manual confirmation” (「人工确认」) + “Rule-based decisions” (「规则判断」). See [Choose your processing mode](user-guide.md#choose-your-processing-mode) for the combinations.

### 12. Why is a subscribed item still not transferred automatically?

Check that it is the same title and season and that TMDB matching succeeded. Also confirm that the source supports subscription-based approval, confidence, filters, and share information pass the checks, and the content is not already fully in the library.

Simply adding a title to the subscriptions table does not bypass identity checks. An unknown season or a multi-season collection will not be forced through by a subscription either. See [Subscriptions](user-guide.md#subscriptions-and-blocking-titles) for automatic-processing requirements.

### 13. I only want subscribed content. Why do other titles still appear for approval?

“Rules + subscription approval” (「规则 + 订阅放行」) only helps new subscribed episodes get approved automatically; it does not exclude other titles.

Enable the separate “Subscriptions only” (「仅处理订阅」) switch. This is supported from **v1.5.0**; earlier versions do not have the setting. You can set a global default or a per-source override, as described in the [user guide](user-guide.md#subscriptions-only). Items that cannot be matched to an active subscription are also ignored when it is enabled, so verify TMDB and your subscriptions first.

### 14. Why are items still processed or missing-episode reminders still sent after I unsubscribe?

Canceling a subscription does not cancel already approved tasks or block the title.

- If the source itself uses automatic processing and “Subscriptions only” is off, later messages may still be transferred under ordinary rules.
- Switches and source settings do not automatically reassess old tasks.
- With “Automatic tracking” (「自动跟踪」) enabled in the missing-episode plugin, recently updated titles may still be tracked.

To stop receiving a title altogether, use “Stop receiving” (「不再接收」). To stop only missing-episode reminders, choose “Stop waiting / Stop reminding” (「不用等了 / 不再提醒」) in the corresponding reminder.

### 15. Why does AI-generated rule creation return invalid?

`invalid` does not necessarily mean a network failure. The model may have returned valid JSON with incorrect fields, types, or structure, or the rules themselves may have failed validation.

The current source includes clearer output-structure requirements and accepts some known alternative response formats. Check the changelog to see whether your release includes the fix. If it still fails:

1. Read the page's specific error and AI call records to distinguish formatting issues, timeouts, and permission problems.
2. Make sure the model is suitable for structured output. If the endpoint genuinely does not support schema, adjust the response format as described in the [configuration reference](configuration.md#identification-and-decisions-defaults-sources).
3. Try simpler, representative examples; if necessary, change the model or write rules manually.

Repeated retries may continue consuming tokens. Even a successfully generated rule must be tested and checked; success alone does not establish correctness.

### 16. Can I avoid AI calls entirely?

A source can use “Rule-based identification” (「规则识别」) with “Rule-based decisions” (「规则判断」) or “Rules + subscription approval” (「规则 + 订阅放行」), without relying on AI to parse messages or decide what to collect. However, if AI remains configured, rule generation, the natural-language assistant, episode-selection assistance during organization, daily reports, and other features may use it.

To avoid all AI calls, leave the AI service unconfigured and do not use AI features. TMDB, Telegram, and 115 remain separate network services; “no AI” does not mean the entire project runs offline.

## 115 transfers and organization

### 17. Why were multiple episodes transferred when the message mentioned only one?

The default episode basis is “Share” (「分享」): use the actual files in the share and transfer episodes within scope that are not yet in the library, rather than relying only on message text. For example, if the same share grows from episode 1 to episode 8 and several episodes are missing from your records, they may be filled in together.

To process only the episodes declared in the message, change the source's episode basis to “Message” (「消息」). The share's files must still pass validation.

### 18. Are ed2k links downloaded to my NAS? Does an unavailable 115 share automatically fall back to ed2k?

No. ed2k links are sent to 115 cloud download, and the files still go to your 115 drive. Completion depends on resource availability and 115's service capabilities.

When both a 115 share and ed2k links are extracted from the same message, the share takes priority. **Reelay does not test whether the share fails and then automatically fall back to ed2k.**

### 19. The transfer keeps waiting for verification. Can I transfer it again?

Do not treat “not yet verified” as “definitely not transferred.” 115 may have accepted the request while the files have not all arrived yet.

Prefer waiting for the background read-only check or using “Verify transfer” (「核验转存」). If some files have already been verified, follow the prompt to keep waiting or “Continue with arrived files” (「按已到的继续」). The latter does not automatically download the missing files.

For recheck timing and stop conditions, see [Deployment and operations](deployment.md#health-checks-and-recovery).

### 20. The transfer finished, but organization failed. What should I do?

Check for incorrect titles or seasons and episodes, filename conflicts, directory permissions, or a temporary drive issue. Use the task's available buttons to correct metadata, resolve conflicts, or reorganize.

“Reorganize” (「重新整理」) uses files already present and does not require another transfer. Overwriting, deleting the files from this task, or abandoning it may affect drive content; read the target and prompt carefully first.

### 21. Will restarting or logging in again help with 115 anti-abuse restrictions?

Handle login expiration and anti-abuse restrictions separately:

- **Expired login**: scan the QR code again in the console; the background service will recover.
- **Too-frequent operations / anti-abuse cooldown**: wait for the cooldown to end. Cooldown state persists across restarts, and repeated retries may not help.

When a large backlog appears, check the number of sources, rules, and scope of automatic processing instead of continually increasing concurrency.

### 22. The task is in the library. Why can Jellyfin / Emby not see it?

Reelay only organizes the files in 115. Your own playback setup determines how the media server reads the drive, how it is mounted or exposed to the server, and when scanning happens.

First confirm the files in 115, then check the media server's access path and scan status. Reelay's “Media library” (「媒体库」) is also a summary of completed library tasks, not a scan of all files in 115.

## Costs, privacy, and data

### 23. Does Reelay cost money? Who receives my messages?

Reelay is free and open source under the MIT license. Hosting, AI tokens, drive capacity, cloud downloading, and other third-party services may cost money according to the provider's terms.

With AI enabled, relevant messages and context are sent to your configured AI service. TMDB receives title queries, while Telegram and 115 handle message reading and drive operations respectively. Self-hosting does not mean these external requests remain on your machine.

“Test identification,” “AI-generated rules,” and some read-only queries may also contact external services. Do not send samples containing private information to an endpoint you do not trust.

### 24. Is backing up the configuration file enough? Will recreating containers lose my data?

Backing up only `config.yaml` is not enough. Subscriptions, library records, task checkpoints, secrets, the Telegram session, and 115 credentials are stored separately within the data directory. Back up the entire data volume.

Normally, recreating containers while retaining the same volume does not clear your data. `docker compose down -v` deletes volumes and must not be used as an ordinary restart command.

Stop the services and back up before upgrading. To roll back after a database upgrade, restore the pre-upgrade data as well as the old image. See [Backup and migration](deployment.md#backups-and-migration).

## What to include in a bug report

First check [GitHub Issues](https://github.com/Derrors/reelay/issues) for an existing report. When opening a new one, include:

- Your version, deployment method, and machine architecture.
- The stage where the problem occurs, what you expected, and what actually happened.
- The task state, processing trace, and time of the error.
- Relevant source rules and confirmation / approval modes, including whether subscription filtering is enabled.
- A **redacted example** that reproduces the problem, if needed.

Do not upload the full `data/` directory, `secrets.json`, a Telegram session, 115 cookies, a Bot Token, AI keys, or request logs containing them. Report vulnerabilities privately under the [security policy](../../SECURITY.md); do not post exploit details in a public issue.
