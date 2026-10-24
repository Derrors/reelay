/** 展示用格式化：时间、季集、媒体类型、比例、海报地址。 */
import { MEDIA_TYPE_LABELS } from './status';

const RTF = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' });

/** 秒级时间戳 → "3 分钟前" / "昨天"；超过 30 天显示日期。 */
export function relativeTime(seconds: number, now = Date.now() / 1000): string {
  const diff = seconds - now;
  const abs = Math.abs(diff);
  if (abs < 45) return '刚刚';
  if (abs < 3600) return RTF.format(Math.round(diff / 60), 'minute');
  if (abs < 86400) return RTF.format(Math.round(diff / 3600), 'hour');
  if (abs < 86400 * 30) return RTF.format(Math.round(diff / 86400), 'day');
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(seconds * 1000));
}

/** 字节 → "2.53 GB" / "850 MB"；未知时为空串。 */
export function sizeText(bytes?: number | null): string {
  if (bytes == null) return '';
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(2)} GB` : `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
}

export function fullTime(seconds: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(seconds * 1000));
}

/** [1,2,3,5] → "E01–E03, E05"；超过 maxParts 段时截断。 */
export function compactEpisodes(episodes: number[] = [], maxParts = 3): string {
  const sorted = [...new Set(episodes)].sort((a, b) => a - b);
  const parts: string[] = [];
  let start: number | null = null;
  let prev: number | null = null;
  const pad = (n: number) => `E${String(n).padStart(2, '0')}`;
  const flush = () => {
    if (start === null || prev === null) return;
    parts.push(start === prev ? pad(start) : `${pad(start)}–${pad(prev)}`);
  };
  for (const ep of sorted) {
    if (prev !== null && ep === prev + 1) { prev = ep; continue; }
    flush();
    start = prev = ep;
  }
  flush();
  return parts.length > maxParts ? `${parts.slice(0, maxParts).join(', ')} 等` : parts.join(', ');
}

/** 跨多季的（季, 集）→ "S01–S03 · 30 集"；季不连续时用顿号分隔。 */
export function seasonsLabel(pairs: number[][]): string {
  const seasons = [...new Set(pairs.map(([s]) => s))].sort((a, b) => a - b);
  const pad = (n: number) => `S${String(n).padStart(2, '0')}`;
  const contiguous = seasons.every((s, i) => s === seasons[0] + i);
  const head = contiguous ? `${pad(seasons[0])}–${pad(seasons[seasons.length - 1])}` : seasons.map(pad).join('、');
  return `${head} · ${new Set(pairs.map(([s, e]) => `${s}:${e}`)).size} 集`;
}

/** 季 + 集的紧凑标签，如 "S01 · E05–E06"；pairs 跨多季时显示 "S01–S03 · 30 集"；电影返回空串。 */
export function episodeLabel(season?: number | null, episodes?: number[] | null, pairs?: number[][] | null): string {
  if (pairs && new Set(pairs.map(([s]) => s)).size > 1) return seasonsLabel(pairs);
  const eps = episodes ?? [];
  if (season == null && !eps.length) return '';
  const s = season != null ? `S${String(season).padStart(2, '0')}` : '';
  const e = compactEpisodes(eps);
  return [s, e].filter(Boolean).join(' · ');
}

export function mediaTypeLabel(type?: string | null): string {
  return (type && MEDIA_TYPE_LABELS[type]) || '未知类型';
}

/** 比例 → 「95%」；没有数据时为「—」。 */
export const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`;

/** TMDB 海报地址；没有路径时返回 null，由调用方显示生成封面。 */
export function posterUrl(path?: string | null, size: 'w185' | 'w342' | 'w500' = 'w342'): string | null {
  return path ? `https://image.tmdb.org/t/p/${size}${path}` : null;
}

/** 由字符串得到稳定的色相，用于生成封面配色。 */
export function hueOf(text: string): number {
  let hash = 0;
  for (const ch of text) hash = (hash * 31 + (ch.codePointAt(0) ?? 0)) >>> 0;
  return hash % 360;
}
