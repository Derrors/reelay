import { useEffect, useMemo, useState } from 'react';
import {
  HardDrive, CheckCircle2, Loader2, TrendingUp, Crown, Trophy,
  type LucideIcon,
} from 'lucide-react';
import ReactECharts from 'echarts-for-react';
import PageHeader from '@/components/PageHeader';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { extras, type IStatsResp } from '@/api/extras';
import { STATE_META } from '@/lib/status';
import type { TaskState } from '@/lib/types';

/* ---------------- 主题色板 ---------------- */

const STATE_COLOR: Record<string, string> = {
  done: '#10b981',
  stored: '#14b8a6',
  organizing: '#22c55e',
  saving: '#06b6d4',
  queued: '#0ea5e9',
  parsed: '#6366f1',
  received: '#8b5cf6',
  pending_approval: '#f59e0b',
  need_manual: '#f97316',
  failed: '#ef4444',
  rejected: '#e11d48',
  skipped: '#94a3b8',
};
const FALLBACK = ['#0f766e', '#0ea5e9', '#8b5cf6', '#f59e0b', '#ef4444', '#94a3b8'];
const stateColor = (st: string, i: number) => STATE_COLOR[st] ?? FALLBACK[i % FALLBACK.length];
const hexA = (hex: string, a: number) => {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
};

const TOOLTIP = {
  backgroundColor: 'rgba(15, 23, 42, 0.94)',
  borderColor: 'transparent',
  padding: [10, 14],
  textStyle: { color: '#f1f5f9', fontSize: 12 },
  extraCssText: 'border-radius:12px;box-shadow:0 14px 36px -10px rgba(15,23,42,.5);',
};

/* 状态分组：主流程三类聚合 + 其它，避免 12 条线糊在一起 */
const TREND_GROUPS = [
  { key: 'success', name: '已入库', color: '#0d9488', states: ['done', 'stored'] },
  { key: 'running', name: '处理中', color: '#6366f1', states: ['received', 'parsed', 'queued', 'saving', 'organizing'] },
  { key: 'waiting', name: '待确认', color: '#f59e0b', states: ['pending_approval', 'need_manual'] },
  { key: 'failed', name: '失败/忽略', color: '#f43f5e', states: ['failed', 'rejected', 'skipped'] },
] as const;

/* ---------------- 迷你面积（KPI 卡内嵌） ---------------- */

function Sparkline({ data, color }: { data: number[]; color: string }) {
  const option = useMemo(() => ({
    animationDuration: 600,
    grid: { left: 0, right: 0, top: 5, bottom: 0 },
    xAxis: { type: 'category' as const, show: false, data: data.map((_, i) => i), boundaryGap: false },
    yAxis: { type: 'value' as const, show: false, min: 0 },
    tooltip: { show: false },
    series: [{
      type: 'line' as const,
      data,
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 2, color },
      areaStyle: {
        color: {
          type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: hexA(color, 0.30) }, { offset: 1, color: hexA(color, 0.02) }],
        },
      },
    }],
  }), [data, color]);
  return <ReactECharts option={option} style={{ height: 42, width: '100%' }} opts={{ renderer: 'svg' }} />;
}

/* ---------------- 主组件 ---------------- */

type View = {
  days: string[];
  counts: Record<string, number>;
  total: number; ok: number; active: number; failed: number; successRate: number;
  dailyDone: number[]; dailyActive: number[]; dailyFailed: number[];
  trend: Record<string, number[]>;
  peak: number; avg: number; peakDay: string;
};

export default function StatsPage() {
  const [s, setS] = useState<IStatsResp | null>(null);

  useEffect(() => { void extras.stats(30).then(setS).catch(() => undefined); }, []);

  const view = useMemo<View | null>(() => {
    if (!s) return null;
    const days = Array.from(new Set(s.daily.map((d) => d.d))).sort();
    const counts = s.counts ?? {};
    const total = Object.values(counts).reduce((a, b) => a + Number(b || 0), 0);
    const ok = (counts.done ?? 0) + (counts.stored ?? 0);
    const active = (counts.received ?? 0) + (counts.parsed ?? 0) + (counts.pending_approval ?? 0)
      + (counts.queued ?? 0) + (counts.saving ?? 0) + (counts.organizing ?? 0) + (counts.need_manual ?? 0);
    const failed = counts.failed ?? 0;
    const valueAt = (d: string, st: string) =>
      s.daily.find((x) => x.d === d && x.state === st)?.n ?? 0;
    const sumStates = (d: string, states: readonly string[]) =>
      states.reduce((a, st) => a + valueAt(d, st), 0);
    const dailyTotal = days.map((d) => s.daily.filter((x) => x.d === d).reduce((a, x) => a + x.n, 0));
    const trend: Record<string, number[]> = {};
    for (const g of TREND_GROUPS) trend[g.key] = days.map((d) => sumStates(d, g.states));
    const dailyDone = trend.success;
    const dailyActive = days.map((d) => sumStates(d, ['received', 'parsed', 'pending_approval', 'queued', 'saving', 'organizing', 'need_manual']));
    const dailyFailed = trend.failed;
    const peak = Math.max(0, ...dailyTotal);
    const peakIdx = dailyTotal.indexOf(peak);
    const sum = dailyTotal.reduce((a, b) => a + b, 0);
    return {
      days, counts, total, ok, active, failed,
      successRate: total ? Math.round((ok / total) * 100) : 0,
      dailyDone, dailyActive, dailyFailed, trend, peak,
      avg: days.length ? Math.round(sum / days.length) : 0,
      peakDay: peak >= 0 ? days[peakIdx] : '',
    };
  }, [s]);

  if (!s || !view) return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader eyebrow="转存统计" title="近 30 天转存情况" description="按天查看各状态的任务数量、最常转存的片名，以及已入库资源的估算体积。" />
      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[136px] rounded-2xl" />)}
      </div>
      <Skeleton className="mt-5 h-[380px] rounded-2xl" />
      <div className="mt-5 grid gap-5 xl:grid-cols-5">
        <Skeleton className="h-[460px] rounded-2xl xl:col-span-3" />
        <Skeleton className="h-[460px] rounded-2xl xl:col-span-2" />
      </div>
    </div>
  );

  /* ---------------- KPI ---------------- */

  const kpis: {
    label: string; value: string; unit: string; note: string;
    icon: LucideIcon; chip: string; spark: number[]; color: string;
  }[] = [
    {
      label: '入库体积', value: s.estimated_done_gb.toFixed(1), unit: 'GB',
      note: '按识别到的文件大小累加', icon: HardDrive,
      chip: 'bg-teal-50 text-teal-600 ring-1 ring-teal-100', spark: view.dailyDone, color: '#0d9488',
    },
    {
      label: '已完成', value: String(view.ok), unit: '部',
      note: `整体成功率 ${view.successRate}%`, icon: CheckCircle2,
      chip: 'bg-emerald-50 text-emerald-600 ring-1 ring-emerald-100', spark: view.dailyDone, color: '#10b981',
    },
    {
      label: '处理中', value: String(view.active), unit: '条',
      note: '识别 · 确认 · 转存 · 整理', icon: Loader2,
      chip: 'bg-indigo-50 text-indigo-600 ring-1 ring-indigo-100', spark: view.dailyActive, color: '#6366f1',
    },
    {
      label: '失败 / 待处理', value: String(view.failed), unit: '条',
      note: view.failed ? '可在任务中心重试' : '近 30 天一切顺利', icon: Trophy,
      chip: view.failed ? 'bg-rose-50 text-rose-600 ring-1 ring-rose-100' : 'bg-slate-100 text-slate-500 ring-1 ring-slate-200',
      spark: view.dailyFailed, color: '#f43f5e',
    },
  ];

  /* ---------------- 每日趋势：圆角渐变堆叠柱状 ---------------- */

  const barOption = {
    tooltip: {
      ...TOOLTIP,
      trigger: 'axis' as const,
      axisPointer: {
        type: 'shadow' as const,
        shadowStyle: { color: 'rgba(13,148,136,0.06)' },
      },
      order: 'valueDesc' as const,
    },
    legend: {
      data: TREND_GROUPS.map((g) => g.name),
      top: 2, right: 0, icon: 'roundRect',
      itemWidth: 12, itemHeight: 12, itemGap: 18,
      textStyle: { color: '#64748b', fontSize: 11.5 },
    },
    grid: { left: 8, right: 14, top: 52, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: view.days,
      boundaryGap: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: '#94a3b8', fontSize: 10.5, hideOverlap: true, margin: 12,
        formatter: (v: string) => v.slice(5),
      },
    },
    yAxis: {
      type: 'value' as const,
      minInterval: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,.16)', type: 'dashed' as const } },
      axisLabel: { color: '#94a3b8', fontSize: 10.5 },
    },
    series: TREND_GROUPS.map((g, gi) => ({
      name: g.name,
      type: 'bar' as const,
      stack: 'total',
      barWidth: '58%',
      itemStyle: {
        borderRadius: gi === TREND_GROUPS.length - 1 ? [7, 7, 0, 0] : 0,
        color: {
          type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: hexA(g.color, gi === 0 ? 0.95 : 0.88) },
            { offset: 1, color: hexA(g.color, gi === 0 ? 0.70 : 0.62) },
          ],
        },
      },
      emphasis: { focus: 'series' as const, itemStyle: { color: g.color } },
      data: view.trend[g.key],
    })),
  };

  /* ---------------- 状态环图 ---------------- */

  const distEntries = Object.entries(view.counts)
    .filter(([, v]) => Number(v) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]));
  const pieData = distEntries.map(([k, v], i) => ({
    name: STATE_META[k as TaskState]?.label ?? k,
    value: Number(v),
    itemStyle: { color: stateColor(k, i) },
  }));

  const donutOption = {
    tooltip: {
      ...TOOLTIP,
      trigger: 'item' as const,
      formatter: (p: { name: string; value: number; percent: number }) =>
        `<div style="font-weight:600;margin-bottom:2px">${p.name}</div>${p.value} 条 · 占比 <b>${p.percent}%</b>`,
    },
    series: [{
      type: 'pie' as const,
      radius: ['58%', '82%'],
      center: ['50%', '44%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: '#ffffff', borderWidth: 3, borderRadius: 7 },
      label: { show: false },
      labelLine: { show: false },
      emphasis: {
        scale: true, scaleSize: 6,
        itemStyle: { shadowBlur: 22, shadowColor: 'rgba(13,148,136,.32)' },
      },
      data: pieData,
    }],
  };

  const top = s.top_titles ?? [];
  const medals = [
    { bg: 'linear-gradient(135deg,#fbbf24,#f59e0b)', ring: 'bg-amber-50 text-amber-600 ring-amber-100', bar: 'linear-gradient(90deg,#f59e0b,#fbbf24)' },
    { bg: 'linear-gradient(135deg,#cbd5e1,#94a3b8)', ring: 'bg-slate-100 text-slate-500 ring-slate-200', bar: 'linear-gradient(90deg,#94a3b8,#cbd5e1)' },
    { bg: 'linear-gradient(135deg,#fb923c,#d97706)', ring: 'bg-orange-50 text-orange-600 ring-orange-100', bar: 'linear-gradient(90deg,#d97706,#fb923c)' },
  ];

  return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader
        eyebrow="转存统计"
        title="近 30 天转存情况"
        description="按天查看各状态的任务数量、最常转存的片名，以及已入库资源的估算体积。"
      />

      {/* KPI 卡片 */}
      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k) => {
          const Icon = k.icon;
          return (
            <Card
              key={k.label}
              className="group relative overflow-hidden rounded-2xl border-border/60 shadow-sm transition-all duration-300 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-slate-200/70"
            >
              <CardContent className="p-5 pb-1">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="text-[13px] font-medium text-muted-foreground">{k.label}</div>
                    <div className="mt-2 flex items-baseline gap-1.5">
                      <span className="text-[30px] font-bold leading-none tracking-tight tabular-nums text-slate-900">{k.value}</span>
                      <span className="text-[13px] font-medium text-muted-foreground">{k.unit}</span>
                    </div>
                  </div>
                  <span className={`grid size-10 shrink-0 place-items-center rounded-xl ${k.chip}`}>
                    <Icon className="size-[19px]" />
                  </span>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">{k.note}</p>
                <div className="-mx-2 -mb-1 mt-1 opacity-90">
                  <Sparkline data={k.spark.length ? k.spark : [0, 0]} color={k.color} />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* 每日趋势 */}
      <Card className="mt-5 overflow-hidden rounded-2xl border-border/60 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/50 px-5 py-4 sm:px-6">
          <div>
            <h3 className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
              <span className="grid size-7 place-items-center rounded-lg bg-primary/10 text-primary">
                <TrendingUp className="size-4" />
              </span>
              每日任务趋势
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">按主流程聚合的堆叠柱状图</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 ring-1 ring-slate-100">
              日均 <b className="ml-0.5 text-slate-900 tabular-nums">{view.avg}</b> 条
            </span>
            <span className="rounded-full bg-teal-50 px-3 py-1 text-xs font-medium text-teal-700 ring-1 ring-teal-100">
              峰值 <b className="ml-0.5 tabular-nums">{view.peak}</b> 条/天
            </span>
            {view.peakDay && (
              <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700 ring-1 ring-amber-100">
                高峰日 {view.peakDay.slice(5)}
              </span>
            )}
          </div>
        </div>
        <CardContent className="p-3 sm:p-5">
          <ReactECharts notMerge lazyUpdate style={{ height: 340 }} option={barOption} opts={{ renderer: 'canvas' }} />
        </CardContent>
      </Card>

      {/* Top 榜单 + 状态分布 */}
      <div className="mt-5 grid gap-5 xl:grid-cols-5">
        {/* 高频片名榜 */}
        <Card className="overflow-hidden rounded-2xl border-border/60 shadow-sm xl:col-span-3">
          <div className="flex items-center justify-between border-b border-border/50 px-5 py-4 sm:px-6">
            <div>
              <h3 className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                <span className="grid size-7 place-items-center rounded-lg bg-amber-50 text-amber-600 ring-1 ring-amber-100">
                  <Crown className="size-4" />
                </span>
                高频片名 Top {top.length || 15}
              </h3>
              <p className="mt-1 text-xs text-muted-foreground">近 30 天消息中出现次数最多的资源</p>
            </div>
          </div>
          <CardContent className="p-3 sm:p-4">
            {top.length ? (
              <ul className="space-y-1">
                {top.map((x, i) => {
                  const pct = Math.round((x.n / Math.max(1, top[0].n)) * 100);
                  const medal = i < 3 ? medals[i] : null;
                  return (
                    <li
                      key={x.title}
                      className="group relative flex items-center gap-3 overflow-hidden rounded-xl px-3 py-2.5 transition-colors hover:bg-slate-50"
                    >
                      <span
                        className={`grid size-7 shrink-0 place-items-center rounded-lg text-xs font-bold tabular-nums ${
                          medal ? medal.ring : 'bg-slate-100 text-slate-400'
                        }`}
                      >
                        {i + 1}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-3">
                          <span className="truncate text-[13px] font-medium text-slate-700 group-hover:text-slate-900">
                            {x.title}
                          </span>
                          <span className="shrink-0 tabular-nums text-xs font-semibold text-slate-500 group-hover:text-teal-700">
                            {x.n} 次
                          </span>
                        </div>
                        <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{
                              width: `${Math.max(pct, 4)}%`,
                              background: medal
                                ? medal.bar
                                : 'linear-gradient(90deg,#0d9488,#22d3ee)',
                            }}
                          />
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="grid h-[420px] place-items-center text-sm text-muted-foreground">
                暂无数据，监听到资源消息后这里会出现排名
              </div>
            )}
          </CardContent>
        </Card>

        {/* 状态分布 */}
        <Card className="overflow-hidden rounded-2xl border-border/60 shadow-sm xl:col-span-2">
          <div className="border-b border-border/50 px-5 py-4 sm:px-6">
            <h3 className="text-[15px] font-semibold text-slate-900">任务状态分布</h3>
            <p className="mt-1 text-xs text-muted-foreground">近 30 天各状态占比</p>
          </div>
          <CardContent className="p-3 sm:p-4">
            {pieData.length ? (
              <>
                <div className="relative">
                  <ReactECharts notMerge lazyUpdate style={{ height: 232 }} option={donutOption} opts={{ renderer: 'canvas' }} />
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center" style={{ paddingBottom: 26 }}>
                    <span className="text-[34px] font-bold leading-none tabular-nums text-slate-900">{view.successRate}<span className="text-lg text-slate-400">%</span></span>
                    <span className="mt-1 text-xs text-muted-foreground">共 {view.total} 条</span>
                  </div>
                </div>
                <ul className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2">
                  {pieData.map((d) => {
                    const pct = view.total ? Math.round((d.value / view.total) * 100) : 0;
                    return (
                      <li key={d.name} className="flex items-center gap-2 text-xs">
                        <span className="size-2.5 shrink-0 rounded-full ring-2 ring-white" style={{ backgroundColor: d.itemStyle.color }} />
                        <span className="truncate text-slate-600">{d.name}</span>
                        <span className="ml-auto shrink-0 font-semibold tabular-nums text-slate-500">{pct}%</span>
                      </li>
                    );
                  })}
                </ul>
              </>
            ) : (
              <div className="grid h-[420px] place-items-center text-sm text-muted-foreground">暂无任务</div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
