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
    ids: new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1])),
    links: [...html.matchAll(/\b(?:href|src)="([^"]+)"/g)].map(match => match[1]),
  });
}
assert.ok(pages.size >= 13, '没有找到完整文档站构建产物');
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
for (const id of ['仅处理订阅', '仅处理订阅未发布']) {
  assert.ok(pages.get('user-guide.html')?.ids.has(id), `构建后的使用指南缺少订阅锚点：${id}`);
}
console.log(`检查 ${pages.size} 个构建页面及中英文 README、${checked} 个站内链接/锚点/资源：全部通过`);
