import { slug } from 'github-slugger';

export const repository = 'https://github.com/Derrors/reelay';
export const base = '/reelay/';

/** 保留 Markdown 在 GitHub 上的相对链接；仅在网站渲染时转换仓库外层文件。 */
export function siteLink(href, relativePath = 'index.md') {
  if (/^(?:[a-z][a-z\d+.-]*:|\/|#|\?)/i.test(href)) return href;
  const url = new URL(href, `https://docs.invalid/docs/${relativePath}`);
  const path = decodeURIComponent(url.pathname.slice(1));
  if (path === 'README.md') return `/introduction.md${url.search}${url.hash}`;
  if (path.startsWith('docs/')) return href;
  return `${repository}/blob/main/${url.pathname.slice(1)}${url.search}${url.hash}`;
}

export const markdown = {
  // 与仓库中已经使用的 GitHub 中文锚点保持一致，包括下划线、中文括号与数字开头。
  anchor: { slugify: slug },
  toc: { slugify: slug },
  config(md) {
    const link = md.renderer.rules.link_open;
    md.renderer.rules.link_open = (tokens, index, options, env, self) => {
      const token = tokens[index];
      token.attrSet('href', siteLink(token.attrGet('href') || '', env.relativePath));
      return link(tokens, index, options, env, self);
    };
    const fence = md.renderer.rules.fence;
    md.renderer.rules.fence = (tokens, index, options, env, self) => {
      const token = tokens[index];
      if (token.info.trim() !== 'mermaid') return fence(tokens, index, options, env, self);
      // 经 JSON 和 HTML 两层转义，图中的引号、尖括号不会变成 Vue 模板或属性。
      return `<MermaidDiagram :code="${md.utils.escapeHtml(JSON.stringify(token.content))}" />`;
    };
  },
};
