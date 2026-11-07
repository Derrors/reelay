# What is Reelay?

[Quick start](quickstart.md) · [User guide](user-guide.md) · [All documentation](README.md)

## Overview

The channels, groups, and Bots you follow send plenty of media links every day, but you may only want to save a few titles. Repeatedly finding the link, checking whether it contains an episode you need, transferring it to your cloud drive, renaming it, and filing it away takes time.

Reelay is a **self-hosted Telegram → 115 media organizer**. It uses your own Telegram account to monitor selected sources, identifies titles, seasons, episodes, and editions with AI or rules, then checks them against TMDB and the files in the shared content. Eligible items are approved by you or automatically allowed by your rules, transferred to your own 115 account, verified, and organized into folders.

You can configure Reelay in the web management console, and use the Telegram control Bot for everyday approvals, identification corrections, series subscriptions, and reminders. You do not need to keep a browser open, but the background service must keep running. The application UI is currently in Chinese; this English documentation retains key Chinese labels so you can find the corresponding controls.

> **Reel** + **Relay**: passing media from your message feed to your library.

### Who it is for

- **People organizing a media library on a NAS or home server** who want less repetitive transferring, renaming, and filing.
- **People collecting media from multiple Telegram sources** who want one place to filter and review it instead of checking each channel separately.
- **People following ongoing series** who want new episodes transferred according to their rules and a record of what is still missing.
- **People who want control over automation** who prefer to start with individual approval and gradually automate more.

### What Reelay is not

| What you might expect | What Reelay actually does |
| --- | --- |
| Search the entire web for media | It is not a resource search engine and includes no resource catalog. It only processes messages from the sources you configure. TMDB search identifies titles and creates subscriptions; it does not search for download links |
| Automatically find every missing episode | It can track subscriptions, detect missing episodes, and process media received later. It does not actively search the web for missing episodes |
| Transfer between any cloud drives | The current built-in production storage destination is **115**. It is not a transfer tool for Baidu Netdisk, Alibaba Cloud Drive, Google Drive, or OneDrive |
| Download files locally | 115 shares are transferred within the cloud drive; ed2k links are sent to **115 cloud download**, not downloaded to the machine running Reelay |
| Play media immediately after installation | It provides no player, cloud-drive mounting, STRM generation, or Jellyfin / Emby service. You must separately configure how your media server reads 115 files |
| Run unattended without ever making a mistake | AI can misidentify content, and third-party services can rate-limit requests or become unavailable. When information is uncertain or verification fails, automatic processing stops for your review |

## Features

### Identification and filtering

- **Multiple sources**: supports Telegram Bots, channels, and groups accessible to your personal account. A single source card can contain several sources with shared settings.
- **AI or rule-based identification**: extracts titles, years, seasons, episodes, resolutions, and more. Write field rules for each source, or ask AI to draft them from examples.
- **TMDB enrichment**: matches titles, standardizes names, and adds genres, countries and regions, posters, and other details to reduce confusion between works with the same name.
- **115 shares and ed2k links**: extracts links from message text and buttons. When both a 115 share and ed2k links are extracted, the share takes priority; there is no automatic fallback to ed2k if that share is unavailable.
- **Deterministic filters and natural-language preferences**: filter by keywords, media type, region, and resolution. When needed, AI can further restrict the result according to your collection preferences.
- **Test before saving**: “Test identification” (「测试识别」) on a source card can test unsaved changes without creating a transfer task.

### Approvals and subscriptions

- **Individual or batch approval**: use the console or Telegram control Bot to transfer, ignore, correct identification, or change the destination directory.
- **Automatically approve subscribed episodes**: new episodes from the subscribed season can be transferred automatically after confidence, filtering, deduplication, and other checks pass.
- **Subscriptions only**: available since v1.5.0, globally or per source. Content is ignored if it is not subscribed to, the subscription has ended, or it cannot be reliably matched to an active subscription.
- **Stop receiving a title**: unlike ignoring one message, this persistently blocks a title. You can resume receiving it later.
- **Missing-episode reminders and daily reports**: enable the corresponding plugins to see episodes in your library and those still missing. A daily check ends a subscription once airing is finished, all episodes have been collected, and the completion criteria are met.

### Transfer and organization

- **Verification around transfers**: records a checkpoint before requesting the cloud transfer, then reads back the result and confirms the files before organizing them.
- **No blind resubmission of uncertain transfers**: checks existing results first to avoid duplicate transfers. Temporary errors, expired login sessions, and service-imposed cooldowns are handled differently.
- **Automatic classification and naming**: creates directories by media type or two-level category, with naming templates and an organization preview.
- **Duplicate and version-conflict handling**: ask for manual review, keep the existing file, keep both, or upgrade according to version preferences. Enable policies that delete or replace files with care.

### Management and extensions

- **Web, Telegram, and MCP interfaces**: the console is suited to configuration and troubleshooting, Telegram to everyday actions, and MCP to queries and management by external agents.
- **Locally stored data**: configuration, the database, secrets, and sessions are kept together in the data directory for backup and migration.
- **Plugin extensions**: enrichment, share inspection, organization, episode tracking, daily reports, and other capabilities are organized as plugins. You can add your own extensions.
- **Docker deployment**: amd64 / arm64 images are available. You can also run from source on macOS / Linux; Windows requires Docker.

> [!NOTE]
> These docs track the current repository source and may be ahead of the latest release. When using `latest` or a pinned version, check [Releases](https://github.com/Derrors/reelay/releases) and the [changelog](../../CHANGELOG.md).

## How an item reaches your library

| Step | What Reelay does | Where to check or act |
| --- | --- | --- |
| 1. Receive a message | Monitors new messages from configured sources and records them in the database | “Tasks” (「任务」) in the console |
| 2. Identify the media | Extracts links and title information, then performs TMDB matching, share checks, and filtering | The source's “Test identification” (「测试识别」) and the task's processing trace |
| 3. Decide whether to save | Ignores ineligible items, requests approval for uncertain ones, and queues those eligible for automatic approval | “Pending approval” (「待审批」) in the console or a Telegram card |
| 4. Transfer to 115 | Transfers a 115 share or sends ed2k links to 115 cloud download, then verifies the files | Task details and any exception notifications |
| 5. Organize into the library | Classifies, renames, moves, and reads back the files to verify the result | “Media library” (「媒体库」), the 115 directory, and the completion notification |

**“Approved” does not mean “added to library.”** The process is complete only when transfer and organization have finished and the task shows “Added to library” (「已入库」).

## Example directory layout

This is an **illustrative layout**, retaining the Chinese example titles and category names. Actual directories depend on the title metadata, categories, and naming templates. When TMDB matches a title, its ID is included:

```text
电影/
└── 国产电影/
    └── 示例电影 (2026) [tmdbid=…]/
        └── 示例电影 (2026) - 2160p H265.mkv
剧集/
└── 国产剧集/
    └── 示例剧集 (2026) [tmdbid=…]/
        └── Season 01/
            ├── 示例剧集 - S01E01 - 第 1 集 - 1080p H265.mkv
            └── 示例剧集 - S01E02 - 第 2 集 - 1080p H265.mkv
```

The naming format follows the metadata-matching conventions used by media libraries such as Jellyfin / Emby. **Reelay does not mount 115 for your media server or initiate playback.** For directories, templates, and filename conflicts, see [User guide: save directories and organization](user-guide.md#save-directories-and-organization).

## Data, costs, and safety

- **Self-hosted does not mean all processing stays on your machine**: AI features send relevant messages or context to your configured AI endpoint; TMDB matching queries title information; transfers and cloud downloads take place on 115.
- **The project is free and open source; third-party services may charge**: AI tokens, servers, cloud storage capacity, and cloud-download entitlements are governed by their respective providers.
- **Protect the data directory**: it contains the database, secrets, Telegram login session, and 115 credentials. Do not upload it publicly. Back up the entire directory, not just the configuration file.
- **Automation carries account risks**: your personal Telegram account may be restricted, and 115 may impose anti-abuse controls. Rate limiting and verification reduce problems but cannot guarantee absolute safety for accounts or files.
- **Only process content you are entitled to use**: Reelay does not provide, index, or distribute media resources. Read the [disclaimer](../../DISCLAIMER.md) before use.

::: tip First-time setup
Start with “Manual confirmation” (「人工确认」) and rule-only decisions (「规则判断」), and verify one real item first. Once identification, the destination, and naming look right, gradually enable subscription-based approval or automatic processing.

[Follow the quick start →](quickstart.md)
:::
