/** 展示用格式化：时间、季集、媒体类型、海报地址。 */

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

/** 季 + 集的紧凑标签，如 "S01 · E05–E06"；电影返回空串。 */
export function episodeLabel(season?: number | null, episodes?: number[] | null): string {
  const eps = episodes ?? [];
  if (season == null && !eps.length) return '';
  const s = season != null ? `S${String(season).padStart(2, '0')}` : '';
  const e = compactEpisodes(eps);
  return [s, e].filter(Boolean).join(' · ');
}

export const MEDIA_TYPE_LABELS: Record<string, string> = { movie: '电影', tv: '剧集', anime: '动漫' };

export function mediaTypeLabel(type?: string | null): string {
  return (type && MEDIA_TYPE_LABELS[type]) || '未知类型';
}

/** TMDB 海报地址；没有路径时返回 null，由调用方显示生成封面。 */
export function posterUrl(path?: string | null, size: 'w185' | 'w342' | 'w500' = 'w342'): string | null {
  return path ? `https://image.tmdb.org/t/p/${size}${path}` : null;
}

/** 由字符串得到稳定的色相，用于生成封面配色。 */
export function hueOf(text: string): number {
  let hash = 0;
  for (const ch of text) hash = (hash * 31 + ch.codePointAt(0)!) >>> 0;
  return hash % 360;
}
