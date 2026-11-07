import { useState } from 'react';
import { cn } from '@/lib/utils';
import { GROUPS, longDay, shortDay, yTicks, type GroupKey, type StatsView } from './statsView';

const X_LABELS = 8; // 横轴最多显示这么多个日期，其余只留柱子
const AXIS_HEIGHT = 'bottom-6';

/** 每日堆叠柱状图。颜色来自主题变量（GROUPS 的 swatch），切换主题不用重绘；屏幕阅读器与需要精确数字时用表格视图。 */
export function TrendChart({ view, hidden, height }: { view: StatsView; hidden: Set<GroupKey>; height: number }) {
  const [active, setActive] = useState<number | null>(null);
  const groups = GROUPS.filter((g) => !hidden.has(g.key));
  const totals = view.days.map((_, i) => groups.reduce((sum, g) => sum + view.series[g.key][i], 0));
  const ticks = yTicks(Math.max(0, ...totals));
  const top = ticks[ticks.length - 1];
  const every = Math.ceil(view.days.length / X_LABELS);
  const percent = (value: number) => `${(value / top) * 100}%`;

  return (
    <div role="img" aria-label="每日任务趋势，精确数字请切换到表格视图" style={{ height }}
      className="flex gap-2 text-[11px] tabular-nums text-muted-foreground">
      <div className={cn('relative mb-6 shrink-0', top >= 1000 ? 'w-8' : 'w-5')} aria-hidden>
        {ticks.map((tick) => (
          <span key={tick} className="absolute right-0 translate-y-1/2" style={{ bottom: percent(tick) }}>{tick}</span>
        ))}
      </div>
      <div className="relative min-w-0 flex-1" onMouseLeave={() => setActive(null)} aria-hidden>
        <div className={cn('absolute inset-x-0 top-0', AXIS_HEIGHT)}>
          {ticks.map((tick) => (
            <span key={tick} className="absolute inset-x-0 border-t border-border" style={{ bottom: percent(tick) }} />
          ))}
          <div className="absolute inset-0 flex">
            {view.days.map((day, i) => (
              <div key={day} onMouseEnter={() => setActive(i)} onClick={() => setActive(i)}
                className={cn('flex h-full min-w-0 flex-1 items-end justify-center', active === i && 'bg-muted-foreground/8')}>
                <div className="flex w-[62%] max-w-[22px] flex-col-reverse gap-px overflow-hidden rounded-t transition-[height] duration-300"
                  style={{ height: percent(totals[i]) }}>
                  {groups.map((g) => view.series[g.key][i] > 0 && (
                    <span key={g.key} className={cn('block min-h-px', g.swatch)} style={{ flexGrow: view.series[g.key][i] }} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="absolute inset-x-0 bottom-0 flex h-5">
          {view.days.map((day, i) => (
            <span key={day} className="relative min-w-0 flex-1">
              {(view.days.length - 1 - i) % every === 0 ? (
                <span className="absolute left-1/2 -translate-x-1/2 whitespace-nowrap">{shortDay(day)}</span>
              ) : null}
            </span>
          ))}
        </div>
        {active !== null && view.days[active] ? (
          <div className={cn('pointer-events-none absolute top-1 z-10 w-max rounded-[10px] border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-overlay',
            active > view.days.length / 2 ? '-ml-4 -translate-x-full' : 'ml-4')}
            style={{ left: `${((active + 0.5) / view.days.length) * 100}%` }}>
            <div className="mb-0.5 font-semibold">
              {longDay(view.days[active])}
              <span className="ml-2 font-normal text-muted-foreground">共 {totals[active]} 条</span>
            </div>
            {groups.map((g) => {
              const value = view.series[g.key][active];
              return (
                <div key={g.key} className={cn('mt-1 flex items-center gap-2', !value && 'opacity-50')}>
                  <span className={cn('h-[3px] w-2.5 rounded-sm', g.swatch)} />
                  <b className="min-w-4 font-semibold">{value}</b>
                  <span className="text-muted-foreground">{g.label}</span>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}
