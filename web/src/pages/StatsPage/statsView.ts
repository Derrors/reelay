/** 统计页的数据整理：12 个状态聚合成 5 组，按天补齐并汇总。 */
import type { IStatsResp } from '@/api/stats';
import { ACTIVE_STATES } from '@/lib/status';
import type { TaskState } from '@/lib/types';
import type { Token } from './chartPalette';

export type GroupKey = 'done' | 'waiting' | 'active' | 'failed' | 'filtered';
export interface Group { key: GroupKey; label: string; states: TaskState[]; token: Token; alpha?: number; swatch: string }

/**
 * 12 个状态按主流程聚合成 5 组。堆叠顺序即色板顺序（青 → 琥珀 → 蓝 → 红 → 灰）：
 * 青与蓝相邻时肉眼难以区分（dataviz 校验 ΔE < 15），所以把蓝放在琥珀之后，调整顺序前请重新校验。
 */
export const GROUPS: Group[] = [
  { key: 'done', label: '已入库', states: ['done'], token: '--chart-1', swatch: 'bg-chart-1' },
  { key: 'waiting', label: '待处理', states: ['pending_approval', 'need_manual'], token: '--chart-3', swatch: 'bg-chart-3' },
  { key: 'active', label: '处理中', states: ACTIVE_STATES, token: '--chart-2', swatch: 'bg-chart-2' },
  { key: 'failed', label: '失败', states: ['failed'], token: '--chart-5', swatch: 'bg-chart-5' },
  { key: 'filtered', label: '已过滤', states: ['rejected', 'skipped'], token: '--muted-foreground', alpha: 0.45, swatch: 'bg-muted-foreground/45' },
];

const GROUP_OF = new Map<string, GroupKey>(GROUPS.flatMap((g) => g.states.map((s) => [s, g.key] as const)));

export const RANGES = [7, 30, 90] as const;
export type Range = typeof RANGES[number];

const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export const shortDay = (d: string) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`;
export const longDay = (d: string) => `${Number(d.slice(5, 7))} 月 ${Number(d.slice(8, 10))} 日`;

export interface StatsView {
  days: string[];
  series: Record<GroupKey, number[]>;
  rangeTotals: Record<GroupKey, number>;
  counts: Record<GroupKey, number>;
  total: number;
  avg: number;
  peak: number;
  peakDay: string;
}

const perGroup = <T,>(fn: () => T) => Object.fromEntries(GROUPS.map((g) => [g.key, fn()])) as Record<GroupKey, T>;

export function buildView(stats: IStatsResp, range: Range, today = new Date()): StatsView {
  // 没有任务的日子后端不返回，这里补齐为 0，避免横轴把空白日挤掉。
  const dates = new Set(stats.daily.map((row) => row.d));
  for (let i = range - 1; i >= 0; i -= 1) {
    dates.add(ymd(new Date(today.getFullYear(), today.getMonth(), today.getDate() - i)));
  }
  const days = [...dates].sort();
  const index = new Map(days.map((d, i) => [d, i]));
  const series = perGroup(() => days.map(() => 0));
  for (const row of stats.daily) {
    const group = GROUP_OF.get(row.state);
    const i = index.get(row.d);
    if (group && i !== undefined) series[group][i] += row.n;
  }
  const rangeTotals = perGroup(() => 0);
  const counts = perGroup(() => 0);
  for (const g of GROUPS) {
    rangeTotals[g.key] = series[g.key].reduce((a, b) => a + b, 0);
    counts[g.key] = g.states.reduce((a, s) => a + Number(stats.counts[s] ?? 0), 0);
  }
  const daily = days.map((_, i) => GROUPS.reduce((a, g) => a + series[g.key][i], 0));
  const sum = daily.reduce((a, b) => a + b, 0);
  const peak = Math.max(0, ...daily);
  const avg = days.length ? sum / days.length : 0;
  return {
    days, series, rangeTotals, counts,
    total: GROUPS.reduce((a, g) => a + counts[g.key], 0),
    avg: avg >= 10 ? Math.round(avg) : Math.round(avg * 10) / 10,
    peak,
    peakDay: peak > 0 ? days[daily.indexOf(peak)] : '',
  };
}
