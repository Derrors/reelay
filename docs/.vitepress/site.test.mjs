import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createMarkdownRenderer } from 'vitepress';
import { base, markdown, repository, siteLink } from './site.mjs';
import { pairHeadings, withHeadingAliases } from './translations.mjs';

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

test('英文相对链接留在英文目录，仓库文件与 README 正确转换', () => {
  assert.equal(siteLink('quickstart.md#prerequisites', 'en/README.md'), 'quickstart.md#prerequisites');
  assert.equal(siteLink('../../README.en.md#overview', 'en/README.md'), '/en/introduction.md#overview');
  assert.equal(siteLink('../../README.md', 'en/README.md'), '/en/introduction.md');
  assert.equal(siteLink('../../config.example.yaml', 'en/configuration.md'), `${repository}/blob/main/config.example.yaml`);
});

test('每个中文文档都有完整英文对应文件', async () => {
  const files = (await readdir(docs)).filter(file => file.endsWith('.md')).sort();
  const english = (await readdir(path.join(docs, 'en'))).filter(file => file.endsWith('.md')).sort();
  assert.ok(files.length >= 12);
  assert.deepEqual(english, files);
  for (const file of english) {
    const source = await readFile(path.join(docs, 'en', file), 'utf8');
    assert.ok(source.trim().length > 500, `${file}: 英文正文不完整`);
    assert.ok(!/coming soon|translation pending/i.test(source), `${file}: 英文不能只提供占位页`);
  }
});

test('标题配对忽略代码、拒绝章节缺失与层级不一致', async () => {
  const md = await createMarkdownRenderer(docs, markdown, base);
  const zh = md.render('# 示例\n\n## 章节\n\n```sh\n# 不是标题\n```');
  const en = md.render('# Example\n\n## Section');
  assert.deepEqual(pairHeadings(zh, en, 'test.md'), [{ zh: '示例', en: 'example' }, { zh: '章节', en: 'section' }]);
  assert.throws(() => pairHeadings(zh, md.render('# Example'), 'test.md'), /标题数量/);
  assert.throws(() => pairHeadings(zh, md.render('# Example\n\n### Section'), 'test.md'), /标题数量/);
});

test('每一对正文标题在两种语言下均可作为语言切换的锚点', async () => {
  const options = await withHeadingAliases(markdown);
  const md = await createMarkdownRenderer(docs, options, base);
  const files = (await readdir(docs)).filter(file => file.endsWith('.md'));
  for (const file of files) {
    const rendered = await Promise.all([file, `en/${file}`].map(async relativePath => md.render(
      await readFile(path.join(docs, relativePath), 'utf8'), { relativePath, path: path.join(docs, relativePath) },
    )));
    const pairs = pairHeadings(...rendered, file);
    for (const html of rendered) {
      const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
      assert.equal(new Set(ids).size, ids.length, `${file}: 重复锚点`);
      for (const { zh, en } of pairs) {
        assert.ok(ids.includes(zh) && ids.includes(en), `${file}: 缺少对应的中英文标题锚点`);
      }
    }
  }
});

test('中英文 README 的使用文档入口指向在线站点', async () => {
  for (const file of ['README.md', 'README.en.md']) {
    const readme = await readFile(path.join(docs, '..', file), 'utf8');
    const locale = file === 'README.en.md' ? 'en/' : '';
    assert.ok(readme.includes(`https://derrors.github.io/reelay/${locale}quickstart.html`), `${file} 必须提供对应语言的在线快速开始`);
    assert.ok(!/\]\((?:\.\/)?docs\/[^)]+\.md(?:#[^)]*)?\)/.test(readme), `${file} 不应再将使用文档链接到仓库 Markdown`);
  }
});
