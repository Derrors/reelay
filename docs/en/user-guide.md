# User Guide

[Documentation home](README.md) · [Quick start](quickstart.md) · [FAQ](faq.md)

This guide is for users who already have Reelay running. It covers everyday source configuration, approvals, subscriptions, and recovery. If you have not deployed it yet, complete the [quick start](quickstart.md) first. Full field definitions are in the [configuration reference](configuration.md); this page does not require you to write YAML by hand. The console and Bot UI remain in Chinese, so key on-screen labels are included alongside their English explanations.

## Contents

- [Get to know the console](#get-to-know-the-console)
- [Understand processing outcomes](#understand-processing-outcomes)
- [Configure sources](#configure-sources)
- [Choose your processing mode](#choose-your-processing-mode)
- [Approval, corrections, and re-identification](#approval-corrections-and-re-identification)
- [Subscriptions and blocking titles](#subscriptions-and-blocking-titles)
- [Subscriptions only](#subscriptions-only)
- [Save directories and organization](#save-directories-and-organization)
- [Recover from transfer or organization issues](#recover-from-transfer-or-organization-issues)
- [Use Telegram and agents](#use-telegram-and-agents)
- [Routine maintenance checklist](#routine-maintenance-checklist)

## Get to know the console

| Page | Main purpose | Tips |
| --- | --- | --- |
| Overview (「总览」) | Check the background service, connections, pending items, and warnings | Being able to open the page does not mean the Worker and 115 are healthy |
| Pending approval (「待审批」) | Handle items awaiting confirmation (「待确认」) or needing attention (「待人工」) | The former asks whether to save; the latter usually requires resolving a transfer or organization issue |
| Tasks (「任务」) | Inspect each message's state, processing trace, and available actions | Check here when no card arrives instead of assuming the message was not received |
| Media library (「媒体库」) | Summarize titles, seasons, episodes, and paths added by Reelay | It neither scans your entire 115 drive nor plays media |
| Series subscriptions (「追剧订阅」) | Manage followed titles and seasons, view missing episodes, and resume or cancel subscriptions | Each subscription covers one season; the next season is not subscribed automatically |
| Statistics (「统计」) | View processing results, source performance, and AI usage | Helps you decide whether a source is suitable for gradually increasing automation |
| Settings (「设置」) | Manage accounts, sources, save directories, default processing modes, and more | Most settings take effect after saving; a few require restarting the background service |
| Plugins (「插件」) | Manage TMDB enrichment, share inspection, organization, episode tracking, daily reports, and more | Disabling a plugin affects the capabilities that depend on it |
| Tools → 115 directories (「工具 → 115 目录」) | Browse folders, locate directories, and copy directory IDs | Not a full cloud-drive file manager |
| Tools → Runtime logs (「工具 → 运行日志」) | Read logs, search errors, and pause refresh | Redact logs before sharing; clearing logs deletes their history |

## Understand processing outcomes

Reelay does not simply download every link as soon as it arrives. Each item goes through identification, a decision, transfer verification, and organization.

| Visible state | Meaning | Usual next step |
| --- | --- | --- |
| Awaiting identification / Identified (「待识别 / 已识别」) | The message was received, but identification and decision-making are not complete | Wait for the background service; check the Worker if the state remains unchanged for a long time |
| Awaiting confirmation (「待确认」) | Waiting for you to decide whether to save | Check identification, edition, and directory, then approve, ignore, or correct |
| Queued (「排队中」) | Approved for processing and waiting for the background service | Do not approve again or recreate the task |
| Transferring / Transferred / Organizing (「转存中 / 已转存 / 整理中」) | Writing to the drive, verifying, or organizing files | Wait for completion and inspect the trace if needed |
| Added to library (「已入库」) | Transfer and organization are complete and verified | Check the result in the media library and 115 |
| Ignored / Filtered (「已忽略 / 已过滤」) | No transfer was started, possibly because of filtering, duplicates, no subscription, or advertising | Read the specific reason in the trace |
| Failed (「失败」) | A step did not complete | Determine whether the error is temporary or related to configuration or the resource, then use an available action |
| Needs attention (「待人工」) | The automatic workflow cannot safely continue; files may already have arrived | Verify the result or resolve conflicts according to the reason; retry is not always appropriate |

**Available buttons depend on the task state and existing checkpoints.** Tasks that are transferring, already in the library, or have an unresolved transfer do not offer exactly the same actions as ordinary pending-approval tasks.

## Configure sources

### Sources and source cards

Add Bots, channels, or groups under “Settings → Sources” (「设置 → 监听来源」). Enter an `@username` or numeric ID, and make sure the logged-in **personal Telegram account** can already access the source.

A card can contain multiple sources that share confirmation settings, identification rules, filters, save directories, and other options. Group sources with similar message formats; split a source into its own card when it needs different rules.

- New sources start monitoring when added and do not automatically import earlier message history.
- Catch-up after downtime or a disconnection is different: existing sources resume using their saved position, within catch-up limits. See the [configuration reference](configuration.md#telegram-telegram).
- Source-setting changes affect newly received tasks. Existing tasks usually retain the settings from when they arrived. Explicitly choosing “Re-identify” (「重新识别」) uses the current settings.

### Global defaults and source overrides

“Settings → General → Default processing mode” (「设置 → 基础 → 默认处理方式」) provides the defaults. A source-card field inherits the global value unless you explicitly set it; an explicit source setting takes priority.

For example, you can enable Bot notifications globally but disable them for one source. You can also enable “Subscriptions only” (「仅处理订阅」) globally and explicitly disable it for a particular source. **“Off” is different from “Restore default”**: the former overrides the global value; the latter inherits it again.

### Write filter rules

| Setting | How it is evaluated | Common mistake |
| --- | --- | --- |
| Message must contain (「消息必须包含」) | Passes if the message text or links contain any listed item | It does not require every word. Entering only `115` blocks messages containing only ed2k links |
| Ignore if these words appear (「包含这些词直接忽略」) | Skips a message if its text matches any blocked word | Overly broad words may also match legitimate titles or descriptions |
| Allow / exclude rules for type, region, and other fields | Uses fields after identification and TMDB enrichment | Missing metadata does not prove an item meets the conditions |
| Minimum resolution (「最低清晰度」) | Rejects lower resolutions; unknown resolution may require review | An unspecified resolution does not mean the minimum is met |
| Collection preferences (「收藏偏好」) | Applies additional filtering when the approval mode is “AI preference decisions” (「AI 按偏好判断」) | This is neither a destination-directory rule nor a substitute for strict subscription filtering |

To receive both 115 shares and ed2k messages, add `115.com`, `115.cn`, `115cdn.com`, and `ed2k://` as separate entries under “Message must contain.” Still test representative messages from each source.

### Adjust identification rules

When a source is frequently misidentified, fix its rules rather than manually correcting every message:

1. Open “Identification rules” (「识别规则」) on the source card.
2. Fetch recent messages or paste representative examples manually.
3. Write field rules for the title, year, season, episodes, and so on, or ask AI to draft them.
4. Test normal messages, problematic ones, and variations in format.
5. Save the rules once verified, then test the full identification result.

Field rules use Python regular expressions; see the [configuration reference](configuration.md#identification-and-decisions-defaults-sources) for syntax. An AI drafting failure does not mean the resource is invalid or that the rules have been saved. Check the error and, if necessary, write the rules yourself or try another model.

**Test identification is a preview, not a manual resource import.** It runs identification and decision-making using the current draft without creating a transfer task, but it may call AI, TMDB, and share-query services.

## Choose your processing mode

### What the four settings control

| Setting | Question it answers | What it does not control |
| --- | --- | --- |
| Identification method (「识别方式」) | How should the message be interpreted as a title, season, episodes, and other fields? | Does not directly decide whether to transfer |
| Confirmation mode (「确认方式」) | Should an otherwise eligible item wait for my confirmation or be processed automatically? | Does not guarantee automatic transfer for every item |
| Approval mode (「放行方式」) | Should decisions use ordinary rules, subscription-based approval, or additional AI preferences? | Does not determine the final directory structure |
| Subscriptions only (「仅处理订阅」, since v1.5.0) | Should all unsubscribed content be excluded? | Does not mean subscribed content is transferred unconditionally |

“AI processing” (「AI 处理」) is the UI label for a **confirmation mode**; it does not require AI parsing. You can choose rule-based identification and enable automatic processing, or use AI identification and manually confirm every item.

### Recommended combinations

| Goal | Confirmation mode | Approval mode | Subscriptions only |
| --- | --- | --- | --- |
| Get started by reviewing every item yourself | Manual confirmation (「人工确认」) | Rule-based decisions (「规则判断」) | Off |
| Automatically collect new subscribed episodes; ask about everything else | Manual confirmation (「人工确认」) | Rules + subscription approval (「规则 + 订阅放行」) | Off |
| Receive only subscribed content and automatically add eligible new episodes to the library | Manual confirmation (「人工确认」) | Rules + subscription approval (「规则 + 订阅放行」) | On |
| Receive only subscribed content but decide on each item yourself | Manual confirmation (「人工确认」) | Rule-based decisions (「规则判断」) | On |
| Trust a source and automatically collect eligible items where possible | AI processing (「AI 处理」) | Rule-based decisions (「规则判断」), or AI preference decisions (「AI 按偏好判断」) | As needed |

Combinations with “Subscriptions only” enabled require **v1.5.0 or later**.

Regardless of the combination, advertisement detection, filters, deduplication, confidence checks, and transfer verification still apply. Low confidence, parsing fallback, or unclear share information will not be bypassed merely because automatic processing is enabled. With “Subscriptions only” on, items without a subscription match are ignored earlier.

**AI preference decisions can only make the result more restrictive**: AI may reject an item or require manual confirmation, but it cannot force automatic approval for an item that should wait for you, nor can it change the save directory.

## Approval, corrections, and re-identification

### Review items awaiting confirmation

Open “Pending approval → Awaiting confirmation” (「待审批 → 待确认」), and check these in order:

1. **Correct title**: pay particular attention to the year and TMDB information when titles share a name.
2. **Correct season and episodes**: do not rely only on “complete series” or an episode range in the title.
3. **Desired edition**: check whether resolution, source, and other details meet your requirements.
4. **Links and share status**: extracting a link does not prove the share is accessible, let alone successfully transferred.
5. **Suitable directory**: check the actual preview path, not just the directory alias.

Then approve or ignore the item. Approval only allows the background service to start processing; “Added to library” (「已入库」) is the final success state.

Check the selection before batch approval or ignoring. “Apply suggestions” (「按建议处理」) only acts on selected items with a clear approve or ignore recommendation; items requiring manual review remain. Suggestions do not guarantee correctness.

### Corrections versus re-identification

| Action | When to use it | What happens afterward |
| --- | --- | --- |
| Correct (「修正」) | A few fields, such as the title, year, season, or episodes, are wrong and you know the correct values | Updates the current result without automatically approving it. Failed tasks may still need a retry; tasks needing attention with files already present may still need reorganization |
| Re-identify (「重新识别」) | Source rules have changed, or you want to reassess the message from scratch | Re-enters the workflow using the current source settings and may wait for approval, be ignored, or be approved automatically |

Some actions are restricted when an unverified transfer, an existing transfer manifest, or files already present could cause a new identification result to be applied to an old transfer request.

When entering multiple episode numbers in the web console, separate them with commas as the input requires. Do not assume every field supports range notation such as `E01-E05`. “Correct” (「修正」) on Telegram cards accepts short field descriptions; see the [Telegram guide](agents.md).

## Subscriptions and blocking titles

### Add a subscription

Search for a title and select its season under “Series subscriptions” (「追剧订阅」), or use one of these options:

- Choose “Follow this series” (「追这部剧」) on a resource card. The web console may separately ask whether to transfer related pending items too.
- Click “Subscribe and transfer” (「追剧并转存」) on a Telegram card to subscribe and approve that current item together.
- Send `/subs 片名` to the control Bot, replacing `片名` with a title, then select a season from the TMDB candidates.

A subscription covers **one season of one title**, not all of its future seasons. TMDB search only identifies the title; it does not find resource download links for you.

### Why a subscribed item may still need confirmation

Subscription-based approval requires:

- A source approval mode that supports subscriptions.
- A successful TMDB match corresponding to the subscribed title and season.
- Confidence, filters, and parsing status that meet the requirements for automatic processing.
- Content that is not already fully in the library, with no other safety check blocking approval.

Multi-season collections, an unknown season, parsing fallback, or explicit exclusion by AI preferences can prevent automatic approval. Subscriptions do not bypass these checks.

### Missing episodes, completed subscriptions, and resuming

With the “Missing-episode tracking” (「缺集追更」) plugin enabled, the daily check looks for missing episodes using subscriptions, library records, TMDB, and other information. A subscription is marked complete once airing has finished, all episodes have been collected, and the applicable completion criteria are met. **Its state does not change immediately at the moment airing ends.**

- Completed subscriptions can be resumed in the console.
- `/subs` primarily lists subscriptions still being followed, not completed ones.
- A missing-episode notice is a reminder, not a promise that Reelay will search for and download the missing resource.
- The media library and deduplication rely on Reelay's library records. Reelay does not automatically scan and identify every file you manually add to 115.

If a subscription is shown as “Missing-episode reminders only” (「只提醒缺集」), it is still active but does not grant automatic approval through the subscription mechanism. This mode is currently supported through the API, with no separate switch in the ordinary add-subscription UI. **If the source itself has automatic processing enabled, items may still transfer under its ordinary rules.**

### Ignoring, unsubscribing, muting, and blocking

| Action | Scope | What it does not do |
| --- | --- | --- |
| Ignore one message | Affects only the current task | Does not block the entire title |
| Cancel subscription (「取消订阅」) | Deletes the subscription for that season | Does not automatically cancel approved tasks. Without “Subscriptions only,” later content may still be processed under the source's rules |
| “Stop waiting / Stop reminding” for missing episodes (「不用等了 / 不再提醒」) | Mutes reminders for the corresponding missing episodes or season | Does not disable automatic transfers |
| Stop receiving (「不再接收」) | Blocks future matching messages by title and ignores pending items for the same title | Does not delete files already in the library |
| Resume receiving (「恢复接收」) | Removes the title's block record | Does not automatically replay previously ignored messages |

You can choose “Stop receiving” on a Telegram approval card, then view and restore blocked titles on the console's subscriptions page or with `/ignored`.

<!-- Preserve the legacy documentation bookmark; new links use #subscriptions-only. -->
<a id="仅处理订阅未发布"></a>

## Subscriptions only

> [!NOTE]
> Available since **v1.5.0**. Earlier versions of the console do not have this switch; upgrade first. See the [changelog](../../CHANGELOG.md) for release details.

### Enable it

1. First add the titles and seasons you want under “Series subscriptions” (「追剧订阅」), and confirm TMDB is available.
2. To restrict just one source, turn on “Subscriptions only” (「仅处理订阅」) on its source card.
3. To restrict all sources by default, enable it under “Settings → General → Default processing mode” (「设置 → 基础 → 默认处理方式」). Sources inherit it when unset, but may explicitly turn it off.
4. Use the source's “Test identification” (「测试识别」) with one subscribed and one unsubscribed message, then save.

### How items are evaluated

| Content | Result |
| --- | --- |
| Title and season match an active subscription | Continues through existing filtering, confidence, approval, and deduplication rules |
| No subscription, or the subscription was canceled or completed | Ignored directly, without entering pending approval, being forwarded, or sending identification or approval notifications |
| No TMDB match, or subscription membership cannot be established | Ignored directly rather than sent to you to guess |
| Multi-season collection without a single season specified | Every season involved must have an active subscription; otherwise the entire item is ignored |
| Active “Missing-episode reminders only” subscription | Passes subscription filtering, but this still does not grant unconditional automatic transfer |

This applies to both 115 shares and ed2k links and is off by default. The current subscription system is based on seasons of TV series, so with it enabled, you should not expect movies that cannot match a series subscription to keep entering pending approval.

**How this differs from “Rules + subscription approval”**: subscription approval helps new subscribed episodes get approved automatically; “Subscriptions only” excludes unsubscribed content. Selecting only the former does not mean you receive only subscriptions.

Also note:

- Identifying subscription membership still requires parsing the message and performing configured TMDB or share queries. This is not an intake filter with zero identification cost.
- Ignored messages still retain task records and reasons.
- Changing the switch does not clear existing pending items or cancel transfers already started.
- Adding or resuming a subscription does not automatically replay previously ignored messages. You may explicitly re-identify eligible old tasks to reassess them under the current settings.

## Save directories and organization

### What determines the directory

- **Save directory alias**: maps to a destination in 115, not a local directory.
- **The source's “Save to” (「保存到」)**: selects the directory alias for that source.
- **Organizer-plugin categories**: organize by two-level category, media type, or no category.
- **Naming templates**: determine the title folder, season folder, and filenames.

When the top-level name in a two-level category matches a save directory alias, that directory is used as the root. For example, the 「剧集」 (series) category can go into your configured 「剧集」 directory. Otherwise, the full category path is created under the source's chosen directory. If you explicitly redirect an item during approval to a directory different from its category, it is saved directly under that selected directory without applying the original category subdirectories.

**The most reliable check is the actual path on the card and “Preview organization” (「预览整理效果」)**. Do not infer the final path from the alias alone. For full precedence rules, see [Configuration reference: media-library organization](configuration.md#library-organization-pluginsorganizer).

### Select episodes from the message or the share

| Episode basis | Behavior | Best suited to |
| --- | --- | --- |
| Share (「分享」, default) | Uses filenames in the share and transfers episodes within the current scope that are not yet in the library | A share link that keeps updating, where you also want to fill episode gaps |
| Message (「消息」) | Transfers only episodes declared in the message, still checking them against the actual share files | Strictly following the episode selection in each message |

For example, a message may say “updated to episode 8,” but if the share contains episodes 1–8 that are not yet in your library, the default behavior may transfer several episodes. This is not saving the same episode repeatedly.

When the message explicitly specifies a season, only that season is selected. If filenames, seasons and episodes, or multiple versions of the same episode cannot be resolved, automatic selection stops. A validated AI-assisted plan may be used where needed; files are not selected arbitrarily.

### Handle existing-file conflicts

| Policy | Behavior | Recommendation |
| --- | --- | --- |
| Manual handling (「人工处理」, default) | Pauses for your decision | Keep the default when starting out |
| Skip (「跳过」) | Keeps the existing file and sends the new conflicting file to the recycle bin | Enable only if you consistently want to retain the old file |
| Keep both (「并存」) | Adds a suffix to keep the new file alongside the old one | Use when you want multiple versions |
| Upgrade (「洗版」) | Compares version preferences and replaces the old file only if the new one is better | Involves deletion / replacement; understand the preference rules and back up first |

Organization is not a playback feature. Installing Jellyfin / Emby, configuring access to 115 files, and scanning the media library are part of your own playback setup.

## Recover from transfer or organization issues

Start with the task details: **why processing stopped, whether any files are already present, and which buttons are available**. Different problems need different recovery actions.

| Situation | Preferred action | Avoid |
| --- | --- | --- |
| 115 login expired | Scan the QR code again in Settings and wait for the background service to recover | Deleting tasks or rebuilding the database is unnecessary |
| 115 anti-abuse cooldown | Wait until the recovery time shown on the page | Restarting usually does not remove the cooldown; repeated operations may worsen the problem |
| Share expired or canceled | Check whether the content is still accessible; ignore it if invalid | Retrying an invalid link indefinitely |
| Transfer outcome unknown | Use “Verify transfer” (「核验转存」) or wait for a background read-only check | Treating an unknown outcome as proof that nothing transferred and sending the same request again |
| Only some files verified | Decide whether to keep waiting or “Continue with arrived files” (「按已到的继续」) | Assuming partial continuation automatically downloads the missing files |
| Files transferred but organization failed | Correct metadata or resolve conflicts, then “Reorganize” (「重新整理」) | Creating another download to solve a renaming problem |
| Temporary failure with a retry action available | Resolve the cause, then retry as instructed | Treating retry as an action that applies to every state |

“Continue with arrived files” only processes files already verified. “Abandon and delete” (「放弃并删除」) cleans up staged files and may also cancel the associated cloud download for ed2k tasks. Confirm that you really want to abandon the task before proceeding.

**About ed2k links**: extracting a filename, size, and hash only means the link can be parsed. Whether 115 can find the resource and complete its cloud download depends on the later task result. Reelay does not guarantee available sources and does not download on its own host as a substitute for 115. When both a 115 share and ed2k links are extracted from a message, the share takes priority; a failed share does not automatically fall back to ed2k.

For automatic checks of uncertain transfers and recovery timing for expired login sessions and cooldowns, see [Deployment and operations: health checks and recovery](deployment.md#health-checks-and-recovery).

## Use Telegram and agents

### Common Telegram commands

| Command | Purpose |
| --- | --- |
| `/help` | Show help |
| `/status` | Show runtime status |
| `/pending` | View and handle pending approvals |
| `/failed` | View failed tasks |
| `/subs` | View subscriptions still being followed |
| `/subs 片名` | Search TMDB for a title and select a season to subscribe to; replace `片名` with the title. This does not search for resource links |
| `/ignored` | View blocked titles and resume receiving them |

The control Bot only responds to the configured owner's numeric ID. Everyday buttons and commands do not require AI conversation, although title search and similar features may still contact TMDB or use configured AI to help interpret input.

### Natural language versus MCP

- **Bot natural-language assistant**: can query status and propose actions such as batch approvals. Writes first produce a plan and execute only after you click “Execute” (「执行」). Do not assume ordinary card buttons all have the same additional confirmation step.
- **MCP**: lets an external agent query tasks or use the provided management tools. Start with a read-only token. A read-write token allows tools to perform writes directly; whether you are asked to confirm again depends on the agent client, not on Reelay always showing a confirmation.
- **Neither exposes every console capability**: QR-code login, settings changes, and some verification, reorganization, and conflict-resolution actions still require the console or the corresponding Bot buttons.

For setup, the full command list, and permissions, see [Telegram and agents](agents.md).

## Routine maintenance checklist

### Day to day

- Check Worker, Telegram, and 115 status on the overview.
- Regularly handle “Needs attention” (「待人工」), not just “Awaiting confirmation” (「待确认」).
- Review whether subscriptions and sources are still needed, and remove unused sources.
- Monitor AI usage and source identification errors before expanding automatic processing.

### When changing rules

- Test both valid examples and examples that should be ignored before saving.
- Check the save directory and organization preview; verify changes in a separate directory first.
- Remember that setting changes do not automatically reassess old tasks. Re-identification may trigger the current automatic-approval rules.

### Before upgrading

- Read the changelog and confirm the release includes the feature you need.
- Back up the entire data volume, including the database, configuration, secrets, and sessions.
- Plan rollback around both the application version and the pre-upgrade data backup.
- Do not use `docker compose down -v` to “restart”; it deletes the data volume.

Next: [FAQ](faq.md) · [Deployment and operations](deployment.md) · [Full configuration reference](configuration.md)
