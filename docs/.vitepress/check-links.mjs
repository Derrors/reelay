// 检查真正构建出的 HTML，包括共享正文、侧栏、首页卡片与资源；VitePress 本身只检查页面死链。
import assert from 'node:assert/strict';
import { access, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { base } from './site.mjs';

const dist = fileURLToPath(new URL('./dist/', import.meta.url));
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
let checked = 0;
for (const [file, page] of pages) {
  for (const href of page.links) {
    const url = new URL(href.replaceAll('&amp;', '&'), `https://docs.invalid${base}${file}`);
    if (url.origin !== 'https://docs.invalid') continue;
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
assert.ok(pages.get('introduction.html')?.ids.has('它不是什么'), '构建后的项目介绍必须包含 README 共享正文');
console.log(`检查 ${pages.size} 个构建页面、${checked} 个站内链接/锚点/资源：全部通过`);
