import { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, CheckCircle2, CircleDashed, Clock3, Loader2, RefreshCw, Send,
  XCircle, ListChecks, Cpu, Inbox, Radio,
} from 'lucide-react';
import { api } from '@/api/client';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import type { IDashboard } from '@/lib/types';
import { connectionLabels, modeLabels } from '@/lib/status';
import { Realtime, type ChangeScope } from '@/lib/realtime';

const MODE_HINT: Record<'live' | 'polling' | 'offline', string> = {
  live: '实时',
  polling: '自动刷新',
  offline: '连接中断',
};

export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<IDashboard | null>(null);
  const [error, setError] = useState('');
  const [retryingDeliveries, setRetryingDeliveries] = useState(false);
  const [mode, setMode] = useState<'live' | 'polling' | 'offline'>('offline');
  const realtimeRef = useRef<Realtime | null>(null);

  const retryDeliveries = async () => {
    setRetryingDeliveries(true);
    try {
      const { items } = await api.deliveries();
      let accepted = 0;
      for (const item of items) {
        try {
          await api.retryDelivery(item.id);
          accepted += 1;
        } catch {
          // 已被其他管理端重试的项目不会重复入队。
        }
      }
      toast.info(`已重新发送 ${accepted} 条通知${accepted < items.length ? '，其余通知未能重发，请稍后刷新查看' : ''}`);
      await load();
      realtimeRef.current?.flush();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '重试失败');
    } finally {
      setRetryingDeliveries(false);
    }
  };

  const load = async () => {
    setError('');
    try {
      setDashboard(await api.dashboard());
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取运行状态失败');
      return false;
    }
  };

  useEffect(() => {
    let active = true;
    const refresh = () => {
      api.dashboard().then((result) => {
        if (!active) return;
        setDashboard(result);
        setError('');
      }).catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : '读取运行状态失败');
      });
    };
    refresh();

    // 实时推送：有变化才取数；SSE 不可用时事件总线内部自动降级为自适应轮询。
    const scopes: ChangeScope[] = ['counts', 'deliveries', 'runtime'];
    const realtime = new Realtime({
      scopes,
      onChange: refresh,
      onMode: (next) => { if (active) setMode(next); },
    });
    realtimeRef.current = realtime;
    realtime.start();
    return () => {
      active = false;
      realtime.stop();
      realtimeRef.current = null;
    };
  }, []);

  const stats = dashboard ? [
    { label: '处理中', value: dashboard.active, note: '正在识别、排队或转存', icon: Loader2, tone: 'text-info', chip: 'bg-info/10 text-info' },
    { label: '等你处理', value: dashboard.pending, note: '需要你确认或人工处理', icon: Clock3, tone: 'text-warning', chip: 'bg-warning/10 text-warning' },
    { label: '失败', value: dashboard.failed, note: '可在任务中心重试', icon: XCircle, tone: 'text-destructive', chip: 'bg-destructive/10 text-destructive' },
    { label: '已完成', value: dashboard.done, note: '已转存并核验', icon: CheckCircle2, tone: 'text-success', chip: 'bg-success/10 text-success' },
  ] : [];

  const workerRunning = dashboard ? !dashboard.runtime.worker?.stale && dashboard.runtime.worker?.status === 'running' : false;
  const revisionMatch = dashboard && dashboard.runtime.worker?.config_revision === dashboard.config_revision;

  return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader
        eyebrow="运行总览"
        title="转存台实时状态"
        description="这里汇总每条资源从收消息到入库的处理进度，以及后台服务和各连接的健康情况。"
        action={
          <div className="flex items-center gap-2">
            <span
              className={`hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium sm:inline-flex ${
                mode === 'live'
                  ? 'bg-success/10 text-success'
                  : mode === 'polling'
                    ? 'bg-info/10 text-info'
                    : 'bg-muted text-muted-foreground'
              }`}
              title={mode === 'live' ? '服务端状态变化即时推送' : mode === 'polling' ? '实时通道不可用，已自动改为定时刷新' : '正在重新连接实时通道'}
            >
              <Radio className={`size-3.5 ${mode === 'offline' ? 'opacity-60' : ''}`} />
              {MODE_HINT[mode]}
            </span>
            <Button variant="outline" onClick={() => { void load().then((ok) => { if (ok) toast.success('状态已刷新'); }); }}><RefreshCw className="size-4" />刷新</Button>
          </div>
        }
      />

      {error ? (
        <div className="mt-6 flex items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <AlertTriangle className="size-5 shrink-0" />{error}
        </div>
      ) : null}

      {dashboard?.alerts?.map((alert) => (
        <div key={alert.key} role="alert" className="mt-6 flex items-start gap-3 rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm">
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-warning" />
          <div className="min-w-0">
            <p className="font-medium text-foreground">{alert.text}</p>
            <p className="mt-1 text-xs text-muted-foreground">自 {new Date(alert.since * 1000).toLocaleString()} 起</p>
          </div>
        </div>
      ))}

      {!dashboard ? (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[1, 2, 3, 4].map((item) => <Skeleton key={item} className="h-[116px] rounded-xl" />)}
        </div>
      ) : (
        <>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {stats.map((s) => {
              const Icon = s.icon;
              return (
                <Card key={s.label} className="rounded-xl border-border/70 transition-shadow hover:shadow-md">
                  <CardContent className="flex items-start justify-between p-5">
                    <div>
                      <div className="text-[13px] text-muted-foreground">{s.label}</div>
                      <div className={`mt-2 text-[34px] font-semibold leading-none tracking-tight ${s.tone}`}>{s.value}</div>
                      <p className="mt-2.5 text-xs text-muted-foreground">{s.note}</p>
                    </div>
                    <div className={`grid size-10 shrink-0 place-items-center rounded-xl ${s.chip}`}>
                      <Icon className={`size-5 ${s.label === '处理中' && workerRunning ? 'animate-spin' : ''}`} />
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          <div className="mt-6 grid gap-6 xl:grid-cols-[1.15fr_1fr]">
            <Card className="rounded-xl border-border/70">
              <CardContent className="p-5 sm:p-6">
                <div className="flex items-center gap-2 text-[15px] font-semibold">
                  <ListChecks className="size-[18px] text-primary" />一条资源的处理旅程
                </div>
                <div className="mt-4 space-y-0">
                  {[
                    ['收到消息', dashboard.counts.received ?? 0, '监听到 Telegram 新消息，先记录下来'],
                    ['AI 识别', dashboard.counts.parsed ?? 0, '识别片名、清晰度并判断是否转存'],
                    ['等你确认', dashboard.counts.pending_approval ?? 0, '确认后才会开始转存'],
                    ['转存与整理', (dashboard.counts.queued ?? 0) + (dashboard.counts.saving ?? 0), '转进 115，按媒体库规范改名归档'],
                    ['完成入库', dashboard.counts.done ?? 0, '文件已确认存在于网盘中'],
                  ].map(([label, value, note], index) => (
                    <div key={String(label)} className="grid grid-cols-[28px_1fr_auto] items-center gap-3 border-b border-border/60 py-3 last:border-b-0">
                      <div className="flex size-6 items-center justify-center rounded-full bg-primary/8 text-[11px] font-semibold text-primary">{index + 1}</div>
                      <div className="min-w-0">
                        <div className="text-[13.5px] font-medium">{label}</div>
                        <div className="truncate text-xs text-muted-foreground">{note}</div>
                      </div>
                      <div className="text-right text-lg font-semibold tabular-nums text-primary">{value}</div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <div className="space-y-6">
              <Card className="rounded-xl border-border/70">
                <CardContent className="p-5 sm:p-6">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-[15px] font-semibold">
                      <Cpu className="size-[18px] text-primary" />后台服务
                    </div>
                    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${workerRunning ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'}`}>
                      <span className={`size-1.5 rounded-full ${workerRunning ? 'bg-success' : 'bg-warning'}`} />
                      {workerRunning ? '运行中' : '未在运行'}
                    </span>
                  </div>
                  <dl className="mt-4 space-y-3 text-[13px]">
                    <div className="flex items-center justify-between gap-3">
                      <dt className="text-muted-foreground">配置生效状态</dt>
                      <dd className="flex items-center gap-1.5">
                        {revisionMatch
                          ? <><CheckCircle2 className="size-4 text-success" />已是最新</>
                          : <><Clock3 className="size-4 text-warning" />等待后台应用</>}
                      </dd>
                    </div>
                    {dashboard.runtime.worker?.updated_at ? (
                      <div className="flex items-center justify-between gap-3">
                        <dt className="text-muted-foreground">最近一次心跳</dt>
                        <dd className="text-xs text-muted-foreground">{new Date(dashboard.runtime.worker.updated_at * 1000).toLocaleString()}</dd>
                      </div>
                    ) : null}
                    {dashboard.runtime.worker?.config_error ? (
                      <div className="rounded-lg border border-destructive/25 bg-destructive/5 p-2.5 text-xs text-destructive">{dashboard.runtime.worker.config_error}</div>
                    ) : null}
                  </dl>
                </CardContent>
              </Card>

              <Card className="rounded-xl border-border/70">
                <CardContent className="p-5 sm:p-6">
                  <div className="flex items-center gap-2 text-[15px] font-semibold">
                    <Inbox className="size-[18px] text-primary" />连接状态
                    <span className="ml-auto text-xs font-normal text-muted-foreground">{modeLabels[dashboard.mode] ?? '未知'}</span>
                  </div>
                  <div className="mt-4 space-y-2.5">
                    {Object.entries(dashboard.connections).map(([key, conn]) => (
                      <div key={key} className="flex items-center justify-between text-[13px]">
                        <span>{conn.label}</span>
                        <span className={`flex items-center gap-1.5 text-xs ${conn.status === 'connected' ? 'text-success' : 'text-muted-foreground'}`}>
                          {conn.status === 'connected' ? <CheckCircle2 className="size-4" /> : <CircleDashed className="size-4" />}
                          {connectionLabels[conn.status] ?? '待验证'}
                        </span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>

          <Card className="mt-6 rounded-xl border-border/70">
            <CardContent className="p-5 sm:p-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-[15px] font-semibold">
                  <Send className="size-[18px] text-primary" />通知发送
                </div>
                {(dashboard.deliveries.failed ?? 0) > 0
                  ? <Button size="sm" variant="outline" disabled={retryingDeliveries} onClick={() => { void retryDeliveries(); }}>重新发送失败通知</Button>
                  : null}
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  { l: '等待发送', v: dashboard.deliveries.pending ?? 0 },
                  { l: '发送中', v: dashboard.deliveries.delivering ?? 0 },
                  { l: '发送失败', v: dashboard.deliveries.failed ?? 0, danger: true },
                  { l: '已发送', v: dashboard.deliveries.done ?? 0 },
                ].map((x) => (
                  <div key={x.l} className="rounded-lg bg-muted/50 px-4 py-3 text-center">
                    <div className={`text-xl font-semibold tabular-nums ${x.danger ? 'text-destructive' : ''}`}>{x.v}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">{x.l}</div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
