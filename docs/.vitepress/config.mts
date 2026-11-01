import { readFileSync } from 'node:fs';
import { defineConfig } from 'vitepress';
import { base, markdown, repository } from './site.mjs';
import { withHeadingAliases } from './translations.mjs';

// 两种语言共用仓库里的 Logo、主题和本地搜索，不重复维护资源。
const logo = `data:image/svg+xml,${encodeURIComponent(readFileSync(new URL('../assets/logo.svg', import.meta.url), 'utf8'))}`;

export default defineConfig(async () => ({
  base,
  title: 'Reelay',
  description: '自托管的 Telegram → 115 资源整理工具：监听来源、识别筛选、审批追剧、转存核验与自动整理。',
  lang: 'zh-CN',
  // GitHub Pages 直接提供 .html；深链刷新无需服务端重写。
  cleanUrls: false,
  lastUpdated: true,
  sitemap: { hostname: 'https://derrors.github.io/reelay/' },
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: logo }],
    ['meta', { name: 'theme-color', content: '#207f73' }],
  ],
  markdown: await withHeadingAliases(markdown),
  themeConfig: {
    logo: { src: logo, width: 28, height: 28 },
    siteTitle: 'Reelay',
    socialLinks: [{ icon: 'github', link: repository }],
    search: {
      provider: 'local',
      options: {
        miniSearch: {
          options: {
            // 同一分词器处理中文词语与英文单词；VitePress 按 locale 隔离索引。
            tokenize: (text) => Array.from(new Intl.Segmenter('zh-CN', { granularity: 'word' }).segment(text))
              .filter(part => part.isWordLike).map(part => part.segment),
          },
        },
        locales: {
          root: {
            translations: {
              button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
              modal: {
                displayDetails: '显示详细内容', resetButtonTitle: '清空搜索',
                backButtonTitle: '返回', noResultsText: '没有找到相关内容',
                footer: { selectText: '选择', navigateText: '切换', closeText: '关闭' },
              },
            },
          },
        },
      },
    },
  },
  locales: {
    root: {
      label: '简体中文', lang: 'zh-CN',
      themeConfig: {
        nav: [
          { text: '首页', link: '/' },
          { text: '指南', link: '/introduction', activeMatch: '^/(?:introduction|quickstart|user-guide|README)' },
          { text: '配置与部署', items: [
            { text: '配置参考', link: '/configuration' },
            { text: '部署与运维', link: '/deployment' },
            { text: 'Telegram 与 Agent', link: '/agents' },
          ] },
          { text: '常见问题', link: '/faq' },
          { text: '版本发布', link: `${repository}/releases` },
        ],
        sidebar: [
          { text: '开始使用', items: [
            { text: '什么是 Reelay？', link: '/introduction' },
            { text: '快速开始', link: '/quickstart' },
            { text: '文档导航与术语', link: '/README' },
          ] },
          { text: '日常使用', items: [
            { text: '使用指南', link: '/user-guide' },
            { text: '处理方式怎么选', link: '/user-guide#选择适合你的处理方式' },
            { text: '审批、修正与重新识别', link: '/user-guide#审批修正与重新识别' },
            { text: '追剧订阅与不再接收', link: '/user-guide#追剧订阅与不再接收' },
            { text: '保存目录与整理', link: '/user-guide#保存目录与整理' },
            { text: 'Telegram 与 Agent', link: '/agents' },
          ] },
          { text: '配置与维护', items: [
            { text: '配置参考', link: '/configuration' },
            { text: '部署、升级与备份', link: '/deployment' },
            { text: '常见问题', link: '/faq' },
          ] },
          { text: '参与开发', collapsed: false, items: [
            { text: '架构设计', link: '/architecture' },
            { text: '插件开发', link: '/plugins' },
            { text: '文档站维护', link: '/site-maintenance' },
            { text: '参与贡献', link: `${repository}/blob/main/CONTRIBUTING.md` },
            { text: '更新日志', link: `${repository}/blob/main/CHANGELOG.md` },
          ] },
        ],
        outline: { level: [2, 3], label: '本页目录' },
        editLink: { pattern: `${repository}/edit/main/docs/:path`, text: '在 GitHub 上编辑此页' },
        lastUpdated: { text: '最后更新', formatOptions: { dateStyle: 'medium' } },
        docFooter: { prev: '上一篇', next: '下一篇' },
        langMenuLabel: '切换语言',
        sidebarMenuLabel: '文档导航',
        returnToTopLabel: '返回顶部',
        darkModeSwitchLabel: '切换主题',
        lightModeSwitchTitle: '切换到浅色模式',
        darkModeSwitchTitle: '切换到深色模式',
        skipToContentLabel: '跳转到内容',
        footer: {
          message: `基于 <a href="${repository}/blob/main/LICENSE">MIT 协议</a>开源 · <a href="${repository}/blob/main/DISCLAIMER.md">使用声明</a>`,
          copyright: 'Copyright © 2026-present Derrors',
        },
        notFound: {
          title: '页面未找到', quote: '这页可能已移动。可以搜索文档，或从首页重新开始。',
          linkLabel: '返回首页', linkText: '返回首页',
        },
      },
    },
    en: {
      label: 'English', lang: 'en',
      description: 'A self-hosted Telegram → 115 media workflow: identify, filter, approve, follow series, verify transfers, and organize files.',
      themeConfig: {
        nav: [
          { text: 'Home', link: '/en/' },
          { text: 'Guide', link: '/en/introduction', activeMatch: '^/en/(?:introduction|quickstart|user-guide|README)' },
          { text: 'Setup', items: [
            { text: 'Configuration', link: '/en/configuration' },
            { text: 'Deployment', link: '/en/deployment' },
            { text: 'Telegram & Agents', link: '/en/agents' },
          ] },
          { text: 'FAQ', link: '/en/faq' },
          { text: 'Releases', link: `${repository}/releases` },
        ],
        sidebar: [
          { text: 'Getting started', items: [
            { text: 'What is Reelay?', link: '/en/introduction' },
            { text: 'Quick start', link: '/en/quickstart' },
            { text: 'Documentation & glossary', link: '/en/README' },
          ] },
          { text: 'Everyday use', items: [
            { text: 'User guide', link: '/en/user-guide' },
            { text: 'Choose a processing mode', link: '/en/user-guide#choose-your-processing-mode' },
            { text: 'Approval & corrections', link: '/en/user-guide#approval-corrections-and-re-identification' },
            { text: 'Subscriptions & blocked titles', link: '/en/user-guide#subscriptions-and-blocking-titles' },
            { text: 'Directories & organization', link: '/en/user-guide#save-directories-and-organization' },
            { text: 'Telegram & Agents', link: '/en/agents' },
          ] },
          { text: 'Setup & maintenance', items: [
            { text: 'Configuration reference', link: '/en/configuration' },
            { text: 'Deployment, upgrades & backups', link: '/en/deployment' },
            { text: 'FAQ', link: '/en/faq' },
          ] },
          { text: 'Development', collapsed: false, items: [
            { text: 'Architecture', link: '/en/architecture' },
            { text: 'Plugin development', link: '/en/plugins' },
            { text: 'Documentation maintenance', link: '/en/site-maintenance' },
            { text: 'Contributing (中文)', link: `${repository}/blob/main/CONTRIBUTING.md` },
            { text: 'Changelog (中文)', link: `${repository}/blob/main/CHANGELOG.md` },
          ] },
        ],
        outline: { level: [2, 3], label: 'On this page' },
        editLink: { pattern: `${repository}/edit/main/docs/:path`, text: 'Edit this page on GitHub' },
        lastUpdated: { text: 'Last updated', formatOptions: { dateStyle: 'medium' } },
        docFooter: { prev: 'Previous page', next: 'Next page' },
        langMenuLabel: 'Change language',
        sidebarMenuLabel: 'Menu',
        returnToTopLabel: 'Return to top',
        darkModeSwitchLabel: 'Appearance',
        lightModeSwitchTitle: 'Switch to light theme',
        darkModeSwitchTitle: 'Switch to dark theme',
        skipToContentLabel: 'Skip to content',
        footer: {
          message: `Released under the <a href="${repository}/blob/main/LICENSE">MIT License</a> · <a href="${repository}/blob/main/DISCLAIMER.md">Disclaimer (中文)</a>`,
          copyright: 'Copyright © 2026-present Derrors',
        },
        notFound: {
          title: 'PAGE NOT FOUND', quote: 'This page may have moved. Search the documentation or start again from the home page.',
          linkLabel: 'Go to home', linkText: 'Take me home',
        },
      },
    },
  },
}));
