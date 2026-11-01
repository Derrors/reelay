import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createMarkdownRenderer, disposeMdItInstance } from 'vitepress';

const docs = fileURLToPath(new URL('../', import.meta.url));

/** 用实际 Markdown 渲染结果配对标题，避免正则误识别代码块里的 #。 */
export function pairHeadings(chinese, english, file) {
  const headings = html => [...html.matchAll(/<h([1-6]) id="([^"]+)"[^>]*>/g)]
    .map(([, level, id]) => ({ level, id }));
  const zh = headings(chinese);
  const en = headings(english);
  assert.deepEqual(zh.map(h => h.level), en.map(h => h.level), `${file}: 中英文标题数量、层级和顺序必须一致`);
  return zh.map((heading, index) => ({ zh: heading.id, en: en[index].id }));
}

/** 原生语言菜单保留 hash；互通别名让中文、英文收藏链接都落在对应章节。 */
export async function withHeadingAliases(markdown) {
  // VitePress 缓存唯一渲染器；配对用的实例不能污染正式构建的 base 与插件。
  disposeMdItInstance();
  const renderer = await createMarkdownRenderer(docs, markdown);
  const aliases = new Map();
  const files = (await readdir(docs)).filter(file => file.endsWith('.md'));
  for (const file of files) {
    const paths = [file, `en/${file}`];
    const html = await Promise.all(paths.map(async relativePath => renderer.render(
      await readFile(new URL(`../${relativePath}`, import.meta.url), 'utf8'),
      { relativePath, path: `${docs}${relativePath}` },
    )));
    const pairs = pairHeadings(html[0], html[1], file);
    for (const [index, lang, other] of [[0, 'zh', 'en'], [1, 'en', 'zh']]) {
      const ownIds = new Set([...html[index].matchAll(/\bid="([^"]+)"/g)].map(match => match[1]));
      const entries = pairs.filter(pair => pair[lang] !== pair[other]).map(pair => {
        assert.ok(!ownIds.has(pair[other]), `${paths[index]}: 跨语言锚点冲突 ${pair[other]}`);
        return [pair[lang], pair[other]];
      });
      aliases.set(paths[index], new Map(entries));
    }
  }
  disposeMdItInstance();
  return {
    ...markdown,
    config(md) {
      markdown.config(md);
      const heading = md.renderer.rules.heading_open;
      md.renderer.rules.heading_open = (tokens, index, options, env, self) => {
        const open = heading ? heading(tokens, index, options, env, self) : self.renderToken(tokens, index, options);
        const alias = aliases.get(env.relativePath)?.get(tokens[index].attrGet('id'));
        return open + (alias ? `<span id="${alias}" class="reelay-heading-alias" aria-hidden="true"></span>` : '');
      };
    },
  };
}
