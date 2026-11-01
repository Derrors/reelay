# Documentation maintenance

[Documentation](README.md) · [Contributing (中文)](../../CONTRIBUTING.md)

The Reelay documentation site uses **VitePress** and is hosted at **[https://derrors.github.io/reelay/](https://derrors.github.io/reelay/)**, with English pages under **[https://derrors.github.io/reelay/en/](https://derrors.github.io/reelay/en/)**. It is a standalone static site, not the Reelay web console. It does not need Telegram, 115, an AI service, or production data.

This page describes the build and deployment process in the repository. It does not mean that the current source revision has already been published to that address.

## Local preview

Use Node.js 22.12 or later; CI uses Node.js 24. Run these commands from the repository root:

```bash
npm ci --prefix docs --ignore-scripts
npm run dev --prefix docs
```

Open the address printed in the terminal, usually `http://127.0.0.1:5173/reelay/`, and select English or append `en/`. The development server listens on localhost by default, not on the LAN or the internet.

Check the production build:

```bash
npm test --prefix docs
npm run build --prefix docs
npm run preview --prefix docs
```

The preview address is usually `http://127.0.0.1:4173/reelay/`. Static files are written to `docs/.vitepress/dist/`. Do not commit generated files or `node_modules`.

Documentation dependencies are installed only in `docs/`; they do not change the web console's dependencies in `web/` or the Docker image. `docs/package.json` pins a Vite override that addresses security advisories. Reassess the override when upgrading VitePress, then run tests and a build rather than forcing dependency upgrades.

## Editing documentation

| What to change | Files |
| --- | --- |
| Home page and feature links | `docs/index.md` and `docs/en/index.md` |
| Languages, navigation, sidebar, search and interface text | `docs/.vitepress/config.mts` |
| Colors, fonts and layout | `docs/.vitepress/theme/custom.css` |
| Quick start, user guide, FAQ and other page content | `docs/*.md` and the matching `docs/en/*.md` |
| Repository introduction and short setup instructions | Root `README.md` and `README.en.md`, linking to the online documentation |
| Full project introduction, scope and directory examples | `docs/introduction.md` and `docs/en/introduction.md`, maintained independently |
| Base path, repository link conversion and heading anchors | `docs/.vitepress/site.mjs` |
| Build and deployment | `.github/workflows/docs.yml` |

### Keep pages readable on GitHub and the site

- Continue using relative Markdown links between documents, such as `[User guide](user-guide.md)`.
- Keep relative paths to configuration, code and contribution files outside the documentation directory. The build converts these to GitHub links instead of treating repository files as site pages.
- Heading anchors follow GitHub rules, including Chinese headings, underscores and headings that start with numbers.
- Root READMEs contain only an introduction, key features, short setup instructions and links to the documentation. Maintain the full introduction in `docs/introduction.md` and its English counterpart rather than including README content. Detailed feature and scope links should point directly to the introduction page.
- Keep architecture diagrams in `mermaid` fences. The site renders them in the current theme and retains expandable source code.
- After adding a page, update the sidebar and [documentation index](README.md), then run tests and a build. Do not bypass broken links with `ignoreDeadLinks`.

Full-text search runs locally in the browser against a prebuilt documentation index. It does not use a third-party search service or AI. Clearly mark unreleased features in the page content.

### Keep Chinese and English in sync

- Chinese pages stay in `docs/`, served under `/reelay/`. English pages use the same filenames in `docs/en/`, served under `/reelay/en/`. Every documentation page needs an English counterpart, not just the home page.
- Keep the same number, levels and order of Markdown headings in both languages. During the build, `translations.mjs` pairs headings and adds anchor aliases from the other language, allowing the language menu to preserve the current page and section. Anchor collisions or missing sections fail the build. After changing headings during development, restart `npm run dev --prefix docs` to regenerate aliases; production builds always regenerate them.
- Translate headings naturally and use the current language's anchors for links within the content. Do not translate configuration keys, commands, protocol fields or template placeholders. Keep paragraphs, tables and warnings in sync too: matching headings alone is not a translation review.
- Use `../../` for links from English pages to repository-root files, such as `../../config.example.yaml`. Continue using relative links such as `user-guide.md` between English pages.
- Search indexes are separated by language: Chinese search finds Chinese content, and English search finds English content. Navigation, search prompts, the page outline, the home-page tagline and diagram prompts also follow the selected language.
- Bilingual documentation does not mean the web console or Telegram bot supports English. Retain necessary Chinese interface labels in English instructions. External repository pages such as the contribution guide and changelog may still be in Chinese; label those links accordingly.
- Test language switching on desktop and mobile, refreshing a section URL, search in both languages, diagrams and both themes. The build checker verifies complete page pairs, language attributes and all internal links.

## Enable GitHub Pages for the first time

A repository administrator must complete this one-time setup:

1. Open the repository's **Settings → Pages**.
2. Under **Build and deployment → Source**, select **GitHub Actions**.
3. Merge the documentation site code into `main`.
4. Open **Actions → 文档站** (documentation site) and wait for both **文档检查与构建** (check and build) and **发布到 GitHub Pages** (deploy) to succeed.
5. Open the site using the URL provided by the deployment job.

The documentation workflow does not change the repository's Pages settings. Deployment may fail if Pages is not enabled; enable it and rerun the workflow.

## Updates and checks

- **Pull requests:** run Markdown tests and a production build without publishing.
- **Documentation, root README or workflow changes on `main`:** publish after the checks pass.
- **Manual redeployment:** run the **文档站** workflow in Actions with `main` selected. Other branches build but do not publish.
- **Troubleshooting:** check the build log for broken links or Markdown compilation errors first. Then confirm that Pages uses GitHub Actions and whether the `github-pages` environment requires manual approval.

The site uses the `/reelay/` base path and `.html` page URLs, so readers can open, refresh and bookmark specific sections directly. If you move to a custom domain or rename the repository, update both `base` in `site.mjs` and the sitemap hostname in the configuration; changing only the home-page link is not enough.
