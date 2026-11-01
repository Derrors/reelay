import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createMarkdownRenderer } from 'vitepress';
import { base, markdown, repository, siteLink } from './site.mjs';

const docs = fileURLToPath(new URL('..', import.meta.url));

test('站内链接、外链和锚点不被仓库路径转换改写', () => {
  for (const href of ['quickstart.md', './user-guide.md#仅处理订阅未发布', 'README.md', '#章节', '/faq', 'https://example.com', 'mailto:example@example.com']) {
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
  assert.equal(markdown.anchor.slugify('仅处理订阅（未发布）'), '仅处理订阅未发布');
  assert.equal(markdown.anchor.slugify('目录与来源（save_dirs / sources / providers）'), '目录与来源save_dirs--sources--providers');
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

test('项目介绍使用具名区域共享正文，不依赖固定行号', async () => {
  const intro = await readFile(path.join(docs, 'introduction.md'), 'utf8');
  const readme = await readFile(path.join(docs, '../README.md'), 'utf8');
  assert.ok(intro.includes('<!--@include: ../README.md#introduction-->'));
  const region = readme.match(/<!-- #region introduction -->([\s\S]+?)<!-- #endregion introduction -->/);
  assert.ok(region, 'README 必须提供介绍区域');
  assert.ok(region[1].includes('### 它不是什么'));
  assert.ok(region[1].includes('## 功能特性'));
});
