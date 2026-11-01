# 文档站维护

[文档导航](README.md) · [参与贡献](../CONTRIBUTING.md)

Reelay 文档站使用 **VitePress**，目标地址为 **https://derrors.github.io/reelay/**。网站是独立的静态页面，不是 Reelay 管理台；不需要连接 Telegram、115、AI 服务或生产数据。

本页描述仓库里的构建与部署方式，不代表当前版本已经发布到该地址。

## 本地预览

需要 Node.js 22.12 或更新版本，CI 使用 Node.js 24。在仓库根目录执行：

```bash
npm ci --prefix docs --ignore-scripts
npm run dev --prefix docs
```

按终端提示打开地址，通常为 `http://127.0.0.1:5173/reelay/`。开发服务默认只监听本机，不对局域网或公网开放。

验证正式构建：

```bash
npm test --prefix docs
npm run build --prefix docs
npm run preview --prefix docs
```

预览地址通常为 `http://127.0.0.1:4173/reelay/`。静态文件输出到 `docs/.vitepress/dist/`，不需要提交生成目录或 `node_modules`。

文档站依赖只安装在 `docs/`，不会修改管理台 `web/` 的依赖或 Docker 镜像。`docs/package.json` 单独锁定了修复安全通告的 Vite 版本；升级 VitePress 时应重新评估这个覆盖项，并运行测试与构建，而不是直接执行强制依赖升级。

## 修改文档

| 要修改的内容 | 文件 |
| --- | --- |
| 网站首页和功能入口 | `docs/index.md` |
| 导航、侧栏、搜索和中文界面 | `docs/.vitepress/config.mts` |
| 主色、字体和排版 | `docs/.vitepress/theme/custom.css` |
| 快速开始、使用指南、FAQ 等正文 | 对应的 `docs/*.md` |
| 项目介绍 | 根 `README.md` 中的 `introduction` 区域，网站通过 include 共用正文 |
| 路径前缀、仓库链接转换和标题锚点 | `docs/.vitepress/site.mjs` |
| 构建与发布 | `.github/workflows/docs.yml` |

### 保持 GitHub 与网站都能阅读

- 文档之间继续使用相对 Markdown 链接，例如 `[使用指南](user-guide.md)`。
- 仓库外层的配置、代码或贡献指南继续使用原来的相对路径。构建时会转换为 GitHub 链接，不把代码或配置文件误当成网站页面。
- 标题锚点沿用 GitHub 规则，中文标题、下划线和数字开头的章节在两边保持一致。
- `docs/introduction.md` 复用根 README 的具名区域，不要复制一份完整正文再分别维护。GitHub 不展开 include，直接阅读它提供的 README 链接即可。
- 架构文档继续用 `mermaid` 围栏。网站按当前明暗主题渲染流程图，同时保留可展开的源码。
- 新增页面后补充侧栏与[文档导航](README.md)，再运行测试和构建；不要用 `ignoreDeadLinks` 跳过死链检查。

全文搜索在浏览器本地执行，使用构建好的文档索引，不依赖第三方搜索服务或 AI。未发布能力仍应在正文里明确标记。

## 首次启用 GitHub Pages

需要仓库管理员完成一次设置：

1. 打开仓库 **Settings → Pages**。
2. 在 **Build and deployment → Source** 选择 **GitHub Actions**。
3. 将文档站代码合并到 `main`。
4. 打开 **Actions → 文档站**，等待“文档检查与构建”和“发布到 GitHub Pages”两个任务成功。
5. 通过部署任务给出的地址访问文档站。

文档工作流不会自动修改仓库 Pages 设置。若 Pages 还没启用，发布任务可能失败；设置完成后重新运行即可。

## 后续更新与检查

- **Pull Request**：只执行 Markdown 测试和正式构建，不发布网站。
- **`main` 上的文档、根 README 或工作流变更**：检查通过后发布。
- **手动重发**：在 Actions 里运行“文档站”，分支选 `main`。其他分支只构建，不发布。
- **失败排查**：先看构建日志里的失效链接或 Markdown 编译错误，再检查 Pages 是否采用 GitHub Actions，以及 `github-pages` 环境是否需要人工批准。

站点默认使用 `/reelay/` 子路径和 `.html` 页面地址，支持直接打开、刷新或收藏具体章节。迁移到自定义域名或其他仓库名时，需要同步调整 `site.mjs` 的 `base` 与配置中的 sitemap 地址；不要只改首页链接。
