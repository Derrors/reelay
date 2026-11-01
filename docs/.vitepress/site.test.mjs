import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createMarkdownRenderer } from 'vitepress';
import { base, markdown, repository, siteLink } from './site.mjs';

const docs = fileURLToPath(new URL('..', import.meta.url));

test('站内链接、外链和锚点不被仓库路径转换改写', () => {
  for (const href of ['quickstart.md', './user-guide.md#仅处理订阅', './user-guide.md#仅处理订阅未发布', 'README.md', '#章节', '/faq', 'https://example.com', 'mailto:example@example.com']) {
    assert.equal(siteLink(href, 'README.md'), href);
  }
});

test('根 README 转项目介绍，其他仓库文件保留完整路径与锚点', () => {
  assert.equal(siteLink('../README.md#它不是什么'), '/introduction.md#%E5%AE%83%E4%B8%8D%E6%98%AF%E4%BB%80%E4%B9%88');
  assert.equal(siteLink('../CHANGELOG.md'), `${repository}/blob/main/CHANGELOG.md`);
  assert.equal(siteLink('../reelay/plugins/organizer/category.yaml'), `${repository}/blob/main/reelay/plugins/organizer/category.yaml`);
  assert.equal(siteLink('../../CONTRIBUTING.md#约定', 'nested/page.md'), `${repository}/blob/main/CONTRIBUTING.md#%E7%BA%A6%E5%AE%9A`);
});

test('GitHub 标题锚点保留中文、下划线和数字开头', () => {
  assert.equal(markdown.anchor.slugify('1. 启动服务'), '1-启动服务');
  assert.equal(markdown.anchor.slugify('仅处理订阅'), '仅处理订阅');
  assert.equal(markdown.anchor.slugify('目录与来源（save_dirs / sources / providers）'), '目录与来源save_dirs--sources--providers');
});

test('仅处理订阅文档标明支持版本，站内链接使用新锚点', async () => {
  for (const file of ['../README.md', '../README.en.md', 'README.md', 'introduction.md', 'user-guide.md', 'configuration.md', 'faq.md', 'quickstart.md']) {
    const source = await readFile(path.join(docs, file), 'utf8');
    assert.match(source, /(?:仅处理订阅.*v1\.5\.0|v1\.5\.0.*仅处理订阅|Subscriptions-only.*v1\.5\.0)/, `${file} 必须标明仅处理订阅从 v1.5.0 起支持`);
    assert.ok(!/\]\([^)]*#仅处理订阅未发布\)/.test(source), `${file} 的站内链接应使用新锚点`);
  }
});

test('仅处理订阅的新标题与旧收藏锚点均保留在渲染结果中', async () => {
  const source = await readFile(path.join(docs, 'user-guide.md'), 'utf8');
  const md = await createMarkdownRenderer(docs, markdown, base);
  const env = { path: path.join(docs, 'user-guide.md'), relativePath: 'user-guide.md' };
  const html = md.render(source, env);
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.match(source, /^## 仅处理订阅$/m);
  for (const id of ['仅处理订阅', '仅处理订阅未发布']) {
    assert.equal(ids.filter(value => value === id).length, 1, `仅处理订阅锚点必须存在且唯一：${id}`);
  }
});

test('真实 Markdown 渲染不改写代码，并安全传递 Mermaid 源码', async () => {
  const md = await createMarkdownRenderer(docs, markdown, base);
  const env = { path: path.join(docs, 'architecture.md'), relativePath: 'architecture.md' };
  const source = 'flowchart LR\nA["<example>"] --> B["{{ message }}"]\n';
  const html = md.render(`[配置](../config.example.yaml)\n\n\`../config.example.yaml\`\n\n\`\`\`mermaid\n${source}\`\`\``, env);
  assert.match(html, /href="https:\/\/github.com\/Derrors\/reelay\/blob\/main\/config.example.yaml"/);
  assert.match(html, /<code>\.\.\/config.example.yaml<\/code>/);
  assert.match(html, /<MermaidDiagram :code="&quot;/);
  assert.ok(!html.includes('<example>'));
});

test('完整项目介绍独立维护，不随 README 精简而丢失', async () => {
  const intro = await readFile(path.join(docs, 'introduction.md'), 'utf8');
  assert.ok(!intro.includes('<!--@include:'), '项目介绍不应再依赖 README 的正文');
  for (const heading of ['## 简介', '### 适合谁', '### 它不是什么', '## 功能特性', '## 一条资源如何入库', '## 整理后的目录示例', '## 数据、费用与安全']) {
    assert.ok(intro.includes(heading), `项目介绍缺少 ${heading}`);
  }
});

test('中英文 README 的使用文档入口指向在线站点', async () => {
  for (const file of ['README.md', 'README.en.md']) {
    const readme = await readFile(path.join(docs, '..', file), 'utf8');
    assert.ok(readme.includes('https://derrors.github.io/reelay/quickstart.html'), `${file} 必须提供在线快速开始`);
    assert.ok(!/\]\((?:\.\/)?docs\/[^)]+\.md(?:#[^)]*)?\)/.test(readme), `${file} 不应再将使用文档链接到仓库 Markdown`);
  }
});
