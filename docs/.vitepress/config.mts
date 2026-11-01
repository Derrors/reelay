import { readFileSync } from 'node:fs';
import { defineConfig } from 'vitepress';
import { base, markdown, repository } from './site.mjs';

// 直接读取仓库中已有的 Logo，不维护两份图标。
const logo = `data:image/svg+xml,${encodeURIComponent(readFileSync(new URL('../assets/logo.svg', import.meta.url), 'utf8'))}`;

export default defineConfig({
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
  markdown,
  themeConfig: {
    logo: { src: logo, width: 28, height: 28 },
    siteTitle: 'Reelay',
    nav: [
      { text: '首页', link: '/' },
      { text: '指南', link: '/introduction', activeMatch: '/(?:introduction|quickstart|user-guide|README)' },
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
    search: {
      provider: 'local',
      options: {
        miniSearch: {
          options: {
            // 中文不像英文按空格分词；使「订阅」「核验」能搜到正文中的词。
            tokenize: (text) => Array.from(new Intl.Segmenter('zh-CN', { granularity: 'word' }).segment(text))
              .filter(part => part.isWordLike).map(part => part.segment),
          },
        },
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
    outline: { level: [2, 3], label: '本页目录' },
    editLink: { pattern: `${repository}/edit/main/docs/:path`, text: '在 GitHub 上编辑此页' },
    lastUpdated: { text: '最后更新', formatOptions: { dateStyle: 'medium' } },
    docFooter: { prev: '上一篇', next: '下一篇' },
    sidebarMenuLabel: '文档导航',
    returnToTopLabel: '返回顶部',
    darkModeSwitchLabel: '切换主题',
    lightModeSwitchTitle: '切换到浅色模式',
    darkModeSwitchTitle: '切换到深色模式',
    skipToContentLabel: '跳转到内容',
    socialLinks: [{ icon: 'github', link: repository }],
    footer: {
      message: `基于 <a href="${repository}/blob/main/LICENSE">MIT 协议</a>开源 · <a href="${repository}/blob/main/DISCLAIMER.md">使用声明</a>`,
      copyright: 'Copyright © 2026-present Derrors',
    },
    notFound: {
      title: '页面未找到', quote: '这页可能已移动。可以搜索文档，或从首页重新开始。',
      linkLabel: '返回首页', linkText: '返回首页',
    },
  },
});
