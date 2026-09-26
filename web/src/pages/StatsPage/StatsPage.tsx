import { useEffect, useState } from 'react';
import { HardDrive } from 'lucide-react';
import ReactECharts from 'echarts-for-react';
import PageHeader from '@/components/PageHeader';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { extras, type IStatsResp } from '@/api/extras';

export default function StatsPage() {
  const [s, setS] = useState<IStatsResp | null>(null);

  useEffect(() => { void extras.stats(30).then(setS).catch(() => undefined); }, []);

  if (!s) return <div className="p-8"><Skeleton className="h-96" /></div>;

  const states = Array.from(new Set(s.daily.map((d) => d.state)));
  const days = Array.from(new Set(s.daily.map((d) => d.d))).sort();
  const series = states.map((st) => ({
    name: st, type: 'bar', stack: 't',
    data: days.map((d) => s.daily.find((x) => x.d === d && x.state === st)?.n ?? 0),
  }));
  const countEntries = Object.entries(s.counts ?? {}).filter(([, v]) => Number(v) > 0);

  return (
    <div className="mx-auto w-full max-w-6xl p-5 sm:p-8">
      <PageHeader eyebrow="统计" title="近 30 天转存趋势" description="按天与状态聚合任务量、热门片名，以及已完成资源的估算体积。" />
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <Card className="rounded-md shadow-none"><CardContent className="pt-6">
          <div className="text-xs text-muted-foreground">已完成估算体积</div>
          <div className="mt-1 font-mono text-3xl font-semibold text-primary">{s.estimated_done_gb} GB</div>
          <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground"><HardDrive className="size-3" />依据解析大小累加</p>
        </CardContent></Card>
        {countEntries.slice(0, 2).map(([k, v]) => (
          <Card key={k} className="rounded-md shadow-none"><CardContent className="pt-6">
            <div className="text-xs text-muted-foreground">{k}</div>
            <div className="mt-1 font-mono text-3xl font-semibold">{v}</div>
          </CardContent></Card>
        ))}
      </div>
      <Card className="mt-6 rounded-md shadow-none">
        <CardHeader><CardTitle className="text-base">每日任务（按状态堆叠）</CardTitle></CardHeader>
        <CardContent>
          <ReactECharts style={{ height: 280 }} option={{
            tooltip: { trigger: 'axis' },
            legend: { data: states, top: 0, textStyle: { fontSize: 11 } },
            grid: { left: 40, right: 16, top: 32, bottom: 28 },
            xAxis: { type: 'category', data: days, axisLabel: { fontSize: 10 } },
            yAxis: { type: 'value', minInterval: 1 },
            series,
          }} />
        </CardContent>
      </Card>
      <Card className="mt-6 rounded-md shadow-none">
        <CardHeader><CardTitle className="text-base">高频片名 Top 15</CardTitle></CardHeader>
        <CardContent>
          <ReactECharts style={{ height: 320 }} option={{
            tooltip: { trigger: 'axis' },
            grid: { left: 120, right: 24, top: 8, bottom: 8 },
            xAxis: { type: 'value', minInterval: 1 },
            yAxis: { type: 'category', data: s.top_titles.map((t) => t.title).reverse(), axisLabel: { fontSize: 10 } },
            series: [{ type: 'bar', data: s.top_titles.map((t) => t.n).reverse(), itemStyle: { color: '#2563eb' } }],
          }} />
        </CardContent>
      </Card>
    </div>
  );
}
