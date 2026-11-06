/** 按日期分组（任务列表的「今天 / 昨天 / 11月4日」小标题）。时间是秒级时间戳，按本地时区。 */
const DATE = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric' });
const DATE_WITH_YEAR = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
const WEEKDAY = new Intl.DateTimeFormat('zh-CN', { weekday: 'short' });

function dayStart(ms: number) {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function dayLabel(seconds: number, now = Date.now()): string {
  const day = dayStart(seconds * 1000);
  const today = dayStart(now);
  const diff = Math.round((today - day) / 86_400_000);
  if (diff === 0) return '今天';
  if (diff === 1) return '昨天';
  const date = new Date(day);
  const text = (date.getFullYear() === new Date(now).getFullYear() ? DATE : DATE_WITH_YEAR).format(date);
  return diff > 1 && diff < 7 ? `${text} ${WEEKDAY.format(date)}` : text;
}

/** 已按时间倒序的列表 → [{label, items}]，保持原顺序。 */
export function groupByDay<T>(items: T[], time: (item: T) => number, now = Date.now()): Array<{ label: string; items: T[] }> {
  const groups: Array<{ label: string; items: T[] }> = [];
  for (const item of items) {
    const label = dayLabel(time(item), now);
    const last = groups[groups.length - 1];
    if (last?.label === label) last.items.push(item);
    else groups.push({ label, items: [item] });
  }
  return groups;
}
