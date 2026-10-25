# 免责声明 / Disclaimer

使用 Reelay 即表示你已阅读并接受以下内容。不接受请勿下载、安装或使用。

## 1. 项目性质与用途边界

- Reelay 是一个**自托管的个人自动化工具**：替你阅读你自己的 Telegram 账号**本来就能看到**的消息，识别其中的 115 网盘分享链接，并在你确认后把分享转存到**你自己的** 115 账号、整理成媒体库目录结构。
- 本项目**不提供、不存储、不索引、不分发任何影视或其他内容**，不内置任何资源来源（频道、Bot、群组或网站）。[快速开始](docs/quickstart.md) 中列出的推荐来源由第三方运营，与本项目无关；本项目不对其发布的内容负责，也不保证其合法性与可用性，是否使用由你自行判断。仓库与文档中出现的其他来源名称均为虚构占位符。
- 你应当只用它处理你**有权获取、保存与使用**的内容，例如自己上传的分享、已获授权的内容或公有领域作品。转存、整理、播放什么内容由你自己决定，相关的版权与法律责任由你自行承担。
- 禁止将本项目用于侵犯著作权、传播违法内容、批量爬取或任何违反所在地法律法规的用途。

## 2. Telegram 个人账号（userbot）风险

- 监听功能通过 MTProto 以**你的个人账号**登录（userbot），而不是 Bot API。这是 Telegram 允许的接入方式，但自动化操作可能被 Telegram 风控识别。
- 可能后果包括：触发 `FLOOD_WAIT` 限流、被要求重新验证、账号功能受限，**严重时账号被封禁且无法恢复**。新注册、使用虚拟号码或短时间内频繁登录的账号风险更高。
- 建议：使用已稳定使用一段时间的账号；只监听少量来源；不要用它群发消息或做与本项目无关的自动化；妥善保管 `api_id`/`api_hash` 与 session 文件——session 文件等同于账号登录态，泄露即等于账号被他人登录。

## 3. 115 账号风控风险

- 转存与整理会调用 115 的网页/客户端接口（cookie）。这些接口不是面向第三方开放的稳定 API，随时可能变更或失效。
- 高频请求会触发 115 风控：接口返回"操作频繁/访问上限"、要求重新登录、登录态被踢下线，**严重时账号被限制或封禁**。Reelay 内置分级限流与风控冷却，但不能保证不触发风控。
- 自动审批（`auto` / `default_pass`）、洗版（`plugins.organizer.conflict: upgrade`）和冲突跳过（`skip`）会在你的网盘中自动转存、移动、重命名或删除文件（删除进入 115 回收站）。启用前请确认理解其行为，并定期检查回收站与转存结果。
- cookie 等同于账号密码，Reelay 仅将其保存在本机 `data/` 目录；不要把 `data/` 或其中的任何文件提交到公开位置。

## 4. 第三方服务

- LLM 解析会把消息正文发送到你配置的 OpenAI 兼容接口，TMDB 补全会把片名发送到 TMDB。请自行确认这些服务的隐私条款与费用。
- Reelay 与 Telegram、115、TMDB 及任何 LLM 服务商均无关联，也未获得其认可。

## 5. 无担保

本项目按 [MIT 许可证](LICENSE) "按原样"提供，不附带任何明示或默示的担保。作者与贡献者不对因使用本项目导致的账号封禁、数据丢失、文件误删、费用支出或法律纠纷承担责任。

---

**English summary.** Reelay is a self-hosted personal automation tool. It reads messages your own Telegram account can already see, detects 115 cloud share links, and—after your approval—saves them into your own 115 account. It does not host, index, or distribute any content and ships with no content sources. The source suggested in the quick start is run by a third party and is not affiliated with this project; the project is not responsible for what it posts and makes no guarantee about its legality or availability. Use it only for content you have the right to access and keep; you are solely responsible for what you save. Logging in as a Telegram user (userbot) may trigger rate limits or **account bans**; 115's private web APIs may change at any time and heavy use may get your 115 account **rate-limited, logged out, or restricted**. Automatic approval, quality upgrades, and conflict handling can move, rename, or delete files in your drive. The software is provided "as is" under the MIT License, without warranty of any kind.
