# Reelay Documentation

[Online documentation](https://derrors.github.io/reelay/en/) · [Back to the project](../../README.md)

Reelay identifies messages from the Telegram sources you configure, approves them manually or by rules, transfers the files to your own 115 account, and organizes them into media-library directories. **It is a media processing and organization tool, not a resource search engine or a player.**

You do not need to read every configuration field to get started. Choose a path below based on what you want to do.

## Read by goal

| What you want to do | Suggested reading order |
| --- | --- |
| Decide whether Reelay is right for you | [Overview](introduction.md#overview) → [Capability boundaries](introduction.md#what-reelay-is-not) → [Prerequisites](quickstart.md#before-you-start) |
| Install from scratch and verify your first item | [Quick start](quickstart.md) → [User guide](user-guide.md) |
| Reduce approvals in an existing setup | [Processing-mode combinations](user-guide.md#choose-your-processing-mode) → [Subscriptions](user-guide.md#subscriptions-and-blocking-titles) |
| Process only subscribed series | [Subscriptions only](user-guide.md#subscriptions-only) → [Release history](../../CHANGELOG.md) |
| Fix unexpected identification, filters, or directories | [Sources](user-guide.md#configure-sources) → [Save directories and organization](user-guide.md#save-directories-and-organization) → [Configuration reference](configuration.md) |
| Fix console access, missing messages, or stuck tasks | [FAQ](faq.md) → [Recovery](user-guide.md#recover-from-transfer-or-organization-issues) |
| Upgrade, migrate, or move to another machine | [Deployment and operations](deployment.md) |
| Manage Reelay through Telegram or an external agent | [Telegram and agents](agents.md) |
| Write an extension or contribute code | [Contributing](../../CONTRIBUTING.md) → [Architecture](architecture.md) → [Plugin development](plugins.md) |

## Getting started

- **[Introduction](introduction.md)**: what Reelay is, who it is for, what it supports, and what is outside its scope.
- **[Quick start](quickstart.md)**: prepare accounts, start the console, log in to Telegram, configure your first source, and verify the files in your library.
- **[User guide](user-guide.md)**: practical guidance for sources, approval, corrections, subscriptions, directories, and recovery.
- **[FAQ](faq.md)**: troubleshoot by the stage where a problem occurs instead of repeatedly restarting and hoping it helps.

## Features and configuration

- [Sources and filters](user-guide.md#configure-sources)
- [Manual confirmation, automatic processing, and subscription-based approval](user-guide.md#choose-your-processing-mode)
- [Corrections and re-identification](user-guide.md#approval-corrections-and-re-identification)
- [Subscriptions, missing episodes, cancellation, and blocking titles](user-guide.md#subscriptions-and-blocking-titles)
- [Subscriptions only (since v1.5.0)](user-guide.md#subscriptions-only)
- [Save directories, episode selection, and organization conflicts](user-guide.md#save-directories-and-organization)
- [Telegram commands, natural language, and MCP](agents.md)
- [All configuration fields, defaults, and file locations](configuration.md)

## Deployment and maintenance

- [Docker and local installation](quickstart.md#1-start-the-services)
- [LAN access and the initial password](quickstart.md#2-open-the-console)
- [A host data directory on a NAS](deployment.md#storing-data-in-a-host-directory)
- [Access security and reverse proxies](deployment.md#access-and-security)
- [Versions, upgrades, and rollback](deployment.md#versions-upgrades-and-rollbacks)
- [Backup and migration](deployment.md#backups-and-migration)
- [Health checks and recovery](deployment.md#health-checks-and-recovery)
- [Local documentation preview and GitHub Pages deployment](site-maintenance.md)

## Key terms

| Term | Meaning |
| --- | --- |
| Source | A Telegram Bot, channel, or group you choose to monitor |
| Personal account | The Telegram account used to read source messages; it requires a separate login |
| Control Bot | Your interface to Reelay for cards and commands; it is not the account used to monitor sources |
| Worker | The background process that monitors, identifies, transfers, and organizes. Opening the web console does not replace it |
| Identification method | Whether AI or rules interpret message content |
| Confirmation / approval mode | How the rules determine whether to transfer automatically, wait for approval, or ignore an item |
| Subscription | A title and season you want to follow; a subscription by itself does not exclude all other content |
| Subscriptions only | Available since v1.5.0; directly ignores content that cannot be matched to an active subscription |
| Save directory alias | A name in Reelay mapped to a destination directory ID in 115 |
| Added to library (「已入库」) | The files have been transferred, organized, and verified, not merely approved |
| Needs attention (「待人工」) | The workflow needs your decision before continuing; some files may already have arrived |

## Versions and documentation conventions

- These docs track the current repository source. **Features marked “unreleased” are not yet in release images.** Check [Releases](https://github.com/Derrors/reelay/releases) and the [changelog](../../CHANGELOG.md) for your version.
- `@example_channel`, example titles, and directories are illustrative. Replace them with your own account and drive settings. Sources specifically recommended in the quick start are third parties.
- The management console and Bot UI are currently in Chinese. This guide explains controls in English and retains key Chinese labels. Paths such as “Settings → …” (「设置 → …」) refer to actions in the console; run code blocks only when they apply to your chosen deployment method.
- Dry runs and identification tests are not real transfers. Dry runs use built-in examples; identification tests may query external services but do not create transfer tasks.

## Contributing and security

[Contributing](../../CONTRIBUTING.md) · [Security policy](../../SECURITY.md) · [Disclaimer](../../DISCLAIMER.md) · [MIT license](../../LICENSE)

When reporting a problem, follow the [reporting checklist](faq.md#what-to-include-in-a-bug-report) and provide your version, task state, and a redacted trace. Never publicly share secrets, cookies, login sessions, or the entire data directory.
