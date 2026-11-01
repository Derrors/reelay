// 检查构建出的 HTML 与中英文 README 的在线文档链接；VitePress 本身只检查页面死链。
import assert from 'node:assert/strict';
import { access, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { base } from './site.mjs';

const dist = fileURLToPath(new URL('./dist/', import.meta.url));
const site = new URL(base, 'https://derrors.github.io');
const pages = new Map();
for (const file of await readdir(dist, { recursive: true })) {
  if (!file.endsWith('.html')) continue;
  const html = await readFile(path.join(dist, file), 'utf8');
  pages.set(file, {
    lang: html.match(/<html\b[^>]*\blang="([^"]+)"/)?.[1],
    ids: new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1])),
    headings: [...html.matchAll(/<h[1-6] id="([^"]+)"/g)].map(match => match[1]),
    links: [...html.matchAll(/\b(?:href|src)="([^"]+)"/g)].map(match => match[1]),
  });
}
const documents = (await readdir(new URL('../', import.meta.url))).filter(file => file.endsWith('.md'));
assert.ok(documents.length >= 12, '文档源文件不完整');
for (const file of documents) {
  const target = file.replace(/\.md$/, '.html');
  const zh = pages.get(target);
  const en = pages.get(`en/${target}`);
  assert.ok(zh && en, `${file}: 缺少中英文构建页面`);
  assert.equal(zh.lang, 'zh-CN', `${target}: 中文语言属性错误`);
  assert.equal(en.lang, 'en', `en/${target}: 英文语言属性错误`);
  for (const id of [...zh.headings, ...en.headings]) {
    assert.ok(zh.ids.has(id) && en.ids.has(id), `${file}: 语言切换缺少对应章节 ${id}`);
  }
}
const sources = new Map(pages);
for (const file of ['README.md', 'README.en.md']) {
  const readme = await readFile(new URL(`../../${file}`, import.meta.url), 'utf8');
  sources.set(file, {
    links: [...readme.matchAll(/https:\/\/derrors\.github\.io\/reelay\/[^\s)"'<>]*/g)].map(match => match[0]),
  });
}
let checked = 0;
for (const [file, source] of sources) {
  for (const href of source.links) {
    const url = new URL(href.replaceAll('&amp;', '&'), new URL(file, site));
    if (url.origin !== site.origin) continue;
    assert.ok(url.pathname.startsWith(base), `${file}: 链接离开站点子路径 ${href}`);
    let target = decodeURIComponent(url.pathname.slice(base.length));
    if (!target || target.endsWith('/')) target += 'index.html';
    if (!path.extname(target)) target += '.html';
    assert.ok(!target.split('/').includes('..'), `${file}: 非法路径 ${href}`);
    await access(path.join(dist, target)).catch(() => assert.fail(`${file}: 文件不存在 ${href}`));
    if (url.hash && target.endsWith('.html')) {
      assert.ok(pages.get(target)?.ids.has(decodeURIComponent(url.hash.slice(1))), `${file}: 标题不存在 ${href}`);
    }
    checked++;
  }
}
for (const id of ['简介', '适合谁', '它不是什么', '功能特性', '一条资源如何入库', '整理后的目录示例', '数据费用与安全']) {
  assert.ok(pages.get('introduction.html')?.ids.has(id), `构建后的项目介绍缺少章节：${id}`);
}
for (const target of ['user-guide.html', 'en/user-guide.html']) {
  for (const id of ['仅处理订阅', '仅处理订阅未发布', 'subscriptions-only']) {
    assert.ok(pages.get(target)?.ids.has(id), `${target}: 构建后的使用指南缺少订阅锚点 ${id}`);
  }
}
console.log(`检查 ${pages.size} 个构建页面及中英文 README、${checked} 个站内链接/锚点/资源：全部通过`);
