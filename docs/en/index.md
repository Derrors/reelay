---
layout: home

title: From Telegram messages to your 115 media library
hero:
  name: Reelay
  text: From your message feed,<br>to your own media library
  tagline: A self-hosted Telegram → 115 media organizer. Identify, filter, review, and follow series. Let the background service handle repetitive transfers and renaming, while you stay in control.
  actions:
    - theme: brand
      text: Quick start
      link: /en/quickstart
    - theme: alt
      text: What is Reelay?
      link: /en/introduction
    - theme: alt
      text: GitHub
      link: https://github.com/Derrors/reelay

features:
  - title: Identify and filter incoming messages
    details: Monitor sources with your own Telegram account. AI or rules extract titles, seasons, episodes, and editions, then cross-check them against TMDB and file information.
    link: /en/user-guide#configure-sources
    linkText: Configure your sources
  - title: You decide what to save
    details: Review items individually or in batches, or let rules approve them automatically. Uncertain items wait for you, and identification errors can be corrected and reassessed.
    link: /en/user-guide#choose-your-processing-mode
    linkText: Choose a processing mode
  - title: Follow seasons and track missing episodes
    details: Subscribe to a season to let eligible new episodes through automatically. Enable the episode-tracking plugin to track episodes added to your library and receive missing-episode reminders.
    link: /en/user-guide#subscriptions-and-blocking-titles
    linkText: Learn about subscriptions
  - title: Verify every transfer
    details: Supports 115 shares and ed2k cloud downloads. Saves a checkpoint before transferring; when the outcome is uncertain, verifies it instead of blindly submitting again.
    link: /en/user-guide#recover-from-transfer-or-organization-issues
    linkText: Understand task states
  - title: Organize folders and filenames
    details: File media by type or two-level category, with customizable naming templates. For conflicting versions, review manually, keep the existing file, keep both, or replace according to your preferences.
    link: /en/user-guide#save-directories-and-organization
    linkText: Set up organization
  - title: Configure on the web, manage in Telegram
    details: Use the management console for setup and troubleshooting, the control Bot for approvals, subscriptions, and notifications, and MCP to give external agents query and management tools.
    link: /en/agents
    linkText: Explore the interfaces
---

<div class="home-guide">
  <h2>Start with what you need to do</h2>
  <p class="intro">You do not need to fill in every setting or read the entire manual on day one. Get one item into your library first, then gradually enable automation.</p>
  <div class="reading-paths">
    <a href="./quickstart.html"><strong>Deploying for the first time</strong><span>Prepare your accounts, start the console, add a source, and verify the first item in your library. Every step includes a success check.</span><em>Get started in seven steps →</em></a>
    <a href="./user-guide.html"><strong>Already using Reelay</strong><span>Learn how approval, subscription, and organization settings work in practice, and distinguish approved, queued, and added to library.</span><em>Read the everyday guide →</em></a>
    <a href="./faq.html"><strong>Something is not working</strong><span>Find help by stage, from opening the console and receiving messages to AI identification, 115 rate limits, and organization failures.</span><em>Browse 24 common questions →</em></a>
  </div>
  <div class="home-note"><strong>Know the limits first:</strong> Reelay does not provide a resource catalog, player, cloud-drive mount, or STRM generation. You must separately configure how your media server reads 115 files. These docs track the source code; features marked “unreleased” may not be in a release image. <a href="./introduction.html#what-reelay-is-not">See the capability boundaries</a> · <a href="https://github.com/Derrors/reelay/releases">See released versions</a></div>
</div>
