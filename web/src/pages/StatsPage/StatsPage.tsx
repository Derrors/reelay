import { BarChart, type BarSeriesOption } from 'echarts/charts';
import { GridComponent, type GridComponentOption, TooltipComponent, type TooltipComponentOption } from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import ReactEChartsCore from 'echarts-for-react/esm/core';
import {
  ChartColumn, ChartPie, CheckCircle2, CircleAlert, HardDrive, Hourglass, Table2, Trophy,
} from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { statsApi, type IStatsResp } from '@/api/stats';
import { EmptyState } from '@/components/app/EmptyState';
import { ErrorState, RefreshError } from '@/components/app/ErrorState';
import { Page } from '@/components/app/Page';
import { Panel } from '@/components/app/Panel';
import { Segmented } from '@/components/app/Segmented';
import { StatCard } from '@/components/app/StatCard';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAsync } from '@/hooks/useAsync';
import { useIsMobile } from '@/hooks/use-mobile';
import { STATE_META } from '@/lib/status';
import { cn } from '@/lib/utils';
import { AiUsagePanel, SourceQualityPanel } from './AiPanels';
import { useChartPalette, withAlpha } from './chartPalette';
import { buildView, GROUPS, longDay, RANGES, shortDay, type GroupKey, type Range, type StatsView } from './statsView';

// 只注册用到的图表与组件：整包引入 echarts 约 1 MB
echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);
type EChartsOption = echarts.ComposeOption<BarSeriesOption | GridComponentOption | TooltipComponentOption>;

// 统计按维度分页；ranged 表示受顶部时间范围影响
const TABS = [
  { value: 'overview', label: '概况', ranged: true },
  { value: 'titles', label: '片名排行', ranged: false },
  { value: 'sources', label: '来源质量', ranged: true },
  { value: 'ai', label: 'AI 调用', ranged: true },
] as const;
type TabKey = (typeof TABS)[number]['value'];
const isTab = (value: string | null): value is TabKey => TABS.some((t) => t.value === value);
const TRIGGER_CLASS = [
  'h-9 rounded-md px-3 text-[13px] text-muted-foreground sm:h-8 sm:px-4',
  'data-[state=active]:bg-card data-[state=active]:text-foreground data-[state=active]:shadow-xs',
].join(' ');
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/* ---------------- 每日趋势 ---------------- */

interface TooltipParam { axisValue: string; seriesName: string; value: number; color: string }

function TrendChart({ view, hidden, height }: { view: StatsView; hidden: Set<GroupKey>; height: number }) {
  const palette = useChartPalette();
  const option = useMemo<EChartsOption>(() => {
    const c = palette.colors;
    const visible = GROUPS.filter((g) => !hidden.has(g.key));
    // 每天最上面那一段才做圆角，其余段保持方角，堆叠看起来是一整根柱子。
    const topOf = view.days.map((_, i) => [...visible].reverse().find((g) => view.series[g.key][i] > 0)?.key);
    return {
      animationDuration: 400,
      textStyle: { fontFamily: palette.font },
      grid: { left: 2, right: 8, top: 10, bottom: 2, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: withAlpha(c['--muted-foreground'], 0.08) } },
        backgroundColor: c['--popover'],
        borderColor: c['--border'],
        borderWidth: 1,
        padding: [8, 12],
        textStyle: { color: c['--popover-foreground'], fontSize: 12 },
        extraCssText: 'border-radius:10px;box-shadow:0 12px 32px -12px rgba(0,0,0,.35);',
        formatter: (raw) => {
          const items = raw as unknown as TooltipParam[];
          if (!items.length) return '';
          const total = items.reduce((a, p) => a + (Number(p.value) || 0), 0);
          const rows = items.map((p) => {
            const value = Number(p.value) || 0;
            return `<div style="display:flex;align-items:center;gap:8px;margin-top:4px;${value ? '' : 'opacity:.5'}">`
              + `<span style="width:10px;height:3px;border-radius:2px;background:${p.color}"></span>`
              + `<b style="min-width:16px;font-variant-numeric:tabular-nums">${value}</b>`
              + `<span style="color:${c['--muted-foreground']}">${escapeHtml(p.seriesName)}</span></div>`;
          }).join('');
          return `<div style="font-weight:600;margin-bottom:2px">${escapeHtml(longDay(items[0].axisValue))}`
            + `<span style="margin-left:8px;font-weight:400;color:${c['--muted-foreground']}">共 ${total} 条</span></div>${rows}`;
        },
      },
      xAxis: {
        type: 'category',
        data: view.days,
        axisLine: { lineStyle: { color: c['--border'] } },
        axisTick: { show: false },
        axisLabel: { color: c['--muted-foreground'], fontSize: 11, hideOverlap: true, margin: 10, formatter: shortDay },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitNumber: 4,
        axisLabel: { color: c['--muted-foreground'], fontSize: 11 },
        splitLine: { lineStyle: { color: c['--border'], width: 1 } },
      },
      series: visible.map((g) => {
        const color = g.alpha ? withAlpha(c[g.token], g.alpha) : c[g.token];
        return {
          name: g.label,
          type: 'bar',
          stack: 'total',
          barMaxWidth: 22,
          barCategoryGap: '38%',
          // 用卡片底色描边，在相邻段之间留出 2px 缝隙，而不是画分隔线。
          itemStyle: { color, borderColor: c['--card'], borderWidth: 1 },
          emphasis: { itemStyle: { color, borderColor: c['--card'] } },
          data: view.series[g.key].map((value, i) => (
            topOf[i] === g.key ? { value, itemStyle: { borderRadius: [4, 4, 0, 0] } } : value
          )),
        };
      }),
    };
  }, [palette, view, hidden]);
  return <ReactEChartsCore echarts={echarts} notMerge lazyUpdate option={option} style={{ height }} opts={{ renderer: 'canvas' }} />;
}

function TrendTable({ view }: { view: StatsView }) {
  const rows = view.days
    .map((d, i) => ({ d, values: GROUPS.map((g) => view.series[g.key][i]) }))
    .filter((row) => row.values.some(Boolean))
    .reverse();
  if (!rows.length) return <p className="py-10 text-center text-sm text-muted-foreground">这段时间没有任务</p>;
  return (
    <div className="max-h-[320px] overflow-auto rounded-lg border border-border">
      <table className="w-full text-xs tabular-nums">
        <thead className="sticky top-0 bg-muted text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-left font-medium">日期</th>
            {GROUPS.map((g) => <th key={g.key} className="px-2 py-2 text-right font-medium">{g.label}</th>)}
            <th className="px-3 py-2 text-right font-medium">合计</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.d} className="border-t border-border/70">
              <td className="whitespace-nowrap px-3 py-2 text-left">{shortDay(row.d)}</td>
              {row.values.map((v, i) => (
                <td key={GROUPS[i].key} className={cn('px-2 py-2 text-right', !v && 'text-muted-foreground/50')}>{v}</td>
              ))}
              <td className="px-3 py-2 text-right font-semibold">{row.values.reduce((a, b) => a + b, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrendPanel({ view, range }: { view: StatsView; range: Range }) {
  const [mode, setMode] = useState<'chart' | 'table'>('chart');
  const [hidden, setHidden] = useState<Set<GroupKey>>(() => new Set());
  const isMobile = useIsMobile();
  const toggle = (key: GroupKey) => setHidden((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const empty = GROUPS.every((g) => view.rangeTotals[g.key] === 0);
  return (
    <Panel
      title="每日任务"
      description={`近 ${range} 天，按任务最后更新的日期与当前状态统计`}
      icon={ChartColumn}
      actions={(
        <Segmented<'chart' | 'table'>
          label="显示方式"
          value={mode}
          onChange={setMode}
          options={[
            { value: 'chart', label: <ChartColumn className="size-3.5" />, title: '图表' },
            { value: 'table', label: <Table2 className="size-3.5" />, title: '表格' },
          ]}
        />
      )}
      bodyClassName="p-4 sm:p-5"
    >
      {empty ? (
        <EmptyState icon={ChartColumn} title="这段时间没有任务" description="监听到资源消息后，这里会按天显示处理情况。" className="border-none py-16" />
      ) : (
        <>
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="图例（点击可隐藏）">
              {GROUPS.map((g) => {
                const off = hidden.has(g.key);
                return (
                  <button
                    key={g.key}
                    type="button"
                    aria-pressed={!off}
                    disabled={mode === 'table'}
                    onClick={() => toggle(g.key)}
                    className={cn(
                      'inline-flex min-h-9 items-center gap-2 rounded-md px-2 text-xs transition-colors enabled:hover:bg-muted sm:min-h-8',
                      off && mode === 'chart' && 'opacity-45',
                    )}
                  >
                    <span className={cn('size-2.5 rounded-[3px]', g.swatch)} />
                    <span className="text-foreground/80">{g.label}</span>
                    <span className="font-semibold tabular-nums text-foreground">{view.rangeTotals[g.key]}</span>
                  </button>
                );
              })}
            </div>
            <div className="flex shrink-0 items-center gap-4 px-2 text-xs text-muted-foreground sm:px-0">
              <span>日均 <b className="font-semibold tabular-nums text-foreground">{view.avg}</b> 条</span>
              <span>
                峰值 <b className="font-semibold tabular-nums text-foreground">{view.peak}</b> 条
                {view.peakDay ? <span className="tabular-nums"> · {shortDay(view.peakDay)}</span> : null}
              </span>
            </div>
          </div>
          {mode === 'chart'
            ? <TrendChart view={view} hidden={hidden} height={isMobile ? 240 : 300} />
            : <TrendTable view={view} />}
        </>
      )}
    </Panel>
  );
}

/* ---------------- 片名排行与状态分布 ---------------- */

function TopTitles({ titles }: { titles: IStatsResp['top_titles'] }) {
  const max = Math.max(1, ...titles.map((x) => x.count));
  return (
    <Panel title="出现最多的片名" description="全部任务按片名合并后的次数，深色部分是已入库的" icon={Trophy} bodyClassName="p-3 sm:p-4">
      {titles.length ? (
        <ol className="space-y-0.5 lg:columns-2 lg:gap-8">
          {titles.map((x, i) => (
            <li key={x.title} className="flex break-inside-avoid items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-muted/60">
              <span className={cn(
                'w-5 shrink-0 text-center text-xs font-semibold tabular-nums',
                i < 3 ? 'text-primary' : 'text-muted-foreground',
              )}>
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-[13px] font-medium" title={x.title}>{x.title}</span>
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    <b className="font-semibold text-foreground">{x.count}</b> 次 · 入库 {x.done}
                  </span>
                </div>
                {/* 整条是出现次数，深色部分是其中已入库的 */}
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-primary/10">
                  <div className="h-full rounded-full bg-primary/35" style={{ width: `${Math.max((x.count / max) * 100, 3)}%` }}>
                    <div className="h-full rounded-full bg-primary" style={{ width: `${x.count ? (x.done / x.count) * 100 : 0}%` }} />
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <EmptyState icon={Trophy} title="还没有片名数据" description="识别出片名的任务会在这里按出现次数排名。" className="border-none" />
      )}
    </Panel>
  );
}

function Distribution({ view, stats }: { view: StatsView; stats: IStatsResp }) {
  return (
    <Panel
      title="状态分布"
      description={`全部 ${view.total} 条任务的当前状态`}
      icon={ChartPie}
    >
      {view.total ? (
        <>
          <div className="flex h-3 gap-[2px] overflow-hidden rounded-full" role="img" aria-label="各状态占比">
            {GROUPS.filter((g) => view.counts[g.key] > 0).map((g) => (
              <div
                key={g.key}
                className={cn('h-full first:rounded-l-full last:rounded-r-full', g.swatch)}
                style={{ width: `${(view.counts[g.key] / view.total) * 100}%` }}
                title={`${g.label} ${view.counts[g.key]} 条`}
              />
            ))}
          </div>
          <ul className="mt-5 divide-y divide-border/70">
            {GROUPS.map((g) => {
              const n = view.counts[g.key];
              const parts = g.states.filter((s) => Number(stats.counts[s] ?? 0) > 0);
              return (
                <li key={g.key} className={cn('py-2.5 first:pt-0 last:pb-0', !n && 'opacity-50')}>
                  <div className="flex items-center gap-2.5 text-[13px]">
                    <span className={cn('size-2.5 shrink-0 rounded-[3px]', g.swatch)} />
                    <span className="font-medium">{g.label}</span>
                    <span className="ml-auto font-semibold tabular-nums">{n}</span>
                    <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">
                      {Math.round((n / view.total) * 100)}%
                    </span>
                  </div>
                  {g.states.length > 1 && parts.length ? (
                    <p className="mt-1 pl-5 text-xs text-muted-foreground">
                      {parts.map((s) => `${STATE_META[s].label} ${stats.counts[s]}`).join(' · ')}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <EmptyState icon={ChartPie} title="还没有任务" className="border-none" />
      )}
    </Panel>
  );
}

/* ---------------- 页面 ---------------- */

function StatsSkeleton() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[118px] rounded-xl" />)}
      </div>
      <Skeleton className="h-[420px] rounded-xl" />
      <Skeleton className="h-[260px] rounded-xl" />
    </div>
  );
}

export default function StatsPage() {
  const [range, setRange] = useState<Range>(30);
  const [search, setSearch] = useSearchParams();
  const requested = search.get('tab');
  const tab: TabKey = isTab(requested) ? requested : 'overview';
  const setTab = (value: string) => setSearch((prev) => {
    const next = new URLSearchParams(prev);
    next.set('tab', value);
    return next;
  }, { replace: true });

  const stats = useAsync(() => statsApi.get(range).then((data) => ({ range, data })), [range], '统计数据加载失败');
  const view = useMemo(() => (stats.data ? buildView(stats.data.data, stats.data.range) : null), [stats.data]);
  // 换时间范围时先淡化旧数据，新数据到了再替换
  const refreshing = stats.stale && !stats.error;

  const actions = TABS.find((t) => t.value === tab)?.ranged === false ? null : (
    <Segmented<Range>
      label="时间范围"
      value={range}
      onChange={setRange}
      options={RANGES.map((r) => ({ value: r, label: `近 ${r} 天` }))}
    />
  );

  let body: ReactNode;
  if (!stats.data || !view) {
    body = stats.error
      ? <ErrorState title="统计数据加载失败" error={stats.error} onRetry={stats.reload} retrying={stats.loading} />
      : <StatsSkeleton />;
  } else {
    const { counts } = view;
    const data = stats.data.data;
    const rate = view.total ? Math.round((counts.done / view.total) * 100) : 0;
    const gb = data.estimated_done_gb;
    body = (
      <div className={cn('space-y-5 transition-opacity', refreshing && 'opacity-60')}>
        {stats.error ? <RefreshError error={stats.error} onRetry={stats.reload} className="mb-0" /> : null}
        <Tabs value={tab} onValueChange={setTab} className="gap-5">
          <TabsList className="grid h-auto w-full grid-cols-4 rounded-lg bg-muted p-1 sm:inline-flex sm:w-fit">
            {TABS.map((t) => <TabsTrigger key={t.value} value={t.value} className={TRIGGER_CLASS}>{t.label}</TabsTrigger>)}
          </TabsList>
          <TabsContent value="overview" className="space-y-5">
            <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
              <StatCard
                label="入库体积"
                icon={HardDrive}
                tone="primary"
                value={<>{gb >= 100 ? Math.round(gb) : gb.toFixed(1)}<span className="ml-1 text-sm font-medium text-muted-foreground">GB</span></>}
                hint="按识别到的文件大小估算"
              />
              <StatCard label="已入库" icon={CheckCircle2} tone="success" value={counts.done} hint={`占全部任务 ${rate}%`} />
              <StatCard
                label="待处理"
                icon={Hourglass}
                tone="warning"
                value={counts.waiting}
                hint={counts.waiting ? '等你确认或人工处理' : '没有待处理的任务'}
                to="/inbox"
                highlight={counts.waiting > 0}
              />
              <StatCard
                label="失败"
                icon={CircleAlert}
                tone={counts.failed ? 'danger' : 'neutral'}
                value={counts.failed}
                hint={counts.failed ? '可在任务页查看并重试' : '没有失败的任务'}
                to="/tasks?group=failed"
              />
            </div>
            <TrendPanel view={view} range={stats.data.range} />
            <Distribution view={view} stats={data} />
          </TabsContent>
          <TabsContent value="titles">
            <TopTitles titles={data.top_titles ?? []} />
          </TabsContent>
          <TabsContent value="sources">
            <SourceQualityPanel days={range} />
          </TabsContent>
          <TabsContent value="ai">
            <AiUsagePanel days={range} />
          </TabsContent>
        </Tabs>
      </div>
    );
  }

  return (
    <Page title="统计" description="时间范围作用于每日任务、来源质量与 AI 调用；片名排行与状态分布为累计数据。" actions={actions}>
      {body}
    </Page>
  );
}
