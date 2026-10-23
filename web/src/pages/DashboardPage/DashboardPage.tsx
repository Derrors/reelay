import {
  Activity, AlertTriangle, ArrowRight, Check, CheckCircle2, Clock3, Inbox, LibraryBig, Loader, RefreshCw, Send, X, XCircle,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { MediaCover } from '@/components/app/MediaCover';
import { MediaMeta } from '@/components/app/MediaMeta';
import { Page } from '@/components/app/Page';
import { Panel } from '@/components/app/Panel';
import { StatCard } from '@/components/app/StatCard';
import { ToneBadge } from '@/components/app/StatusBadge';
import { TaskDetailSheet } from '@/components/TaskDetailSheet';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { episodeLabel, fullTime, relativeTime } from '@/lib/format';
import { Realtime } from '@/lib/realtime';
import { connectionLabels, modeLabels, TONE_CLASS, type Tone } from '@/lib/status';
import type { IDashboard, IInboxItem, ITaskSummary } from '@/lib/types';
import { cn } from '@/lib/utils';
import { AiSuggestions } from './AiSuggestions';

interface Snapshot {
  dashboard: IDashboard;
  pending: IInboxItem[];
  recent: ITaskSummary[];
}

function fetchSnapshot(): Promise<Snapshot> {
  return Promise.all([api.dashboard(), api.inbox('pending_approval'), api.tasks({ state: 'done', limit: 8 })])
    .then(([dashboard, inbox, done]) => ({ dashboard, pending: inbox.items, recent: done.items }));
}

const LIVE_LABEL: Record<'live' | 'polling' | 'offline', string> = { live: '实时', polling: '自动刷新', offline: '重新连接中' };

function connectionTone(status: string): Tone {
  if (status === 'connected' || status === 'active' || status === 'running') return 'success';
  if (status === 'not_configured' || status === 'unverified' || status === 'unobserved') return 'neutral';
  if (status === 'connecting') return 'info';
  return 'warning';
}

function HealthRow({ label, status, detail }: { label: string; status: string; detail?: string }) {
  const tone = connectionTone(status);
  return (
    <div className="flex items-center justify-between gap-3 py-2.5 text-sm first:pt-0 last:pb-0">
      <span className="min-w-0 truncate text-foreground/90">{label}</span>
      {/* 状态长短不一：用徽标右对齐，比裸露的圆点更整齐 */}
      <span title={detail} className="shrink-0">
        <ToneBadge tone={tone}>
          {connectionLabels[status] ?? (status === 'active' ? '有效' : status === 'running' ? '运行中' : status)}
        </ToneBadge>
      </span>
    </div>
  );
}

function PendingRow({ item, busy, onOpen, onAct }: {
  item: IInboxItem; busy: boolean; onOpen: () => void; onAct: (action: 'approve' | 'reject') => void;
}) {
  return (
    <div className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
      <button type="button" onClick={onOpen} className="w-10 shrink-0" aria-label={`查看 ${item.title ?? '任务'}`}>
        <MediaCover title={item.title} posterPath={item.poster_path} mediaType={item.media_type} quality="w185" className="rounded-md" />
      </button>
      <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left">
        <div className="truncate text-sm font-medium hover:text-primary">
          {item.title || '尚未识别片名'}
          {item.year ? <span className="ml-1.5 text-xs font-normal text-muted-foreground">{item.year}</span> : null}
        </div>
        <MediaMeta mediaType={item.media_type} season={item.season} episodes={item.episodes} pairs={item.episode_pairs} resolution={item.resolution}
          size={item.size} className="mt-0.5" />
        <div className="mt-0.5 truncate text-xs text-muted-foreground">{item.source} · {relativeTime(item.created_at)}</div>
      </button>
      <div className="flex shrink-0 gap-1.5">
        <Button size="icon" variant="ghost" disabled={busy} onClick={() => onAct('reject')} aria-label="忽略" title="忽略"
          className="size-8 text-muted-foreground hover:text-destructive"><X /></Button>
        <Button size="sm" disabled={busy} onClick={() => onAct('approve')} aria-label="转存"><Check /><span className="max-sm:hidden">转存</span></Button>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'live' | 'polling' | 'offline'>('offline');
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [retrying, setRetrying] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const realtimeRef = useRef<Realtime | null>(null);

  const refresh = useCallback(() => fetchSnapshot()
    .then((next) => { setSnapshot(next); setError(''); })
    .catch((err: unknown) => setError(err instanceof Error ? err.message : '读取运行状态失败')), []);

  useEffect(() => {
    let active = true;
    fetchSnapshot()
      .then((next) => { if (active) setSnapshot(next); })
      .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : '读取运行状态失败'); });
    const realtime = new Realtime({
      scopes: ['counts', 'deliveries', 'runtime', 'tasks'],
      onChange: () => { if (active) void refresh(); },
      onMode: (next) => { if (active) setMode(next); },
    });
    realtimeRef.current = realtime;
    realtime.start();
    return () => { active = false; realtime.stop(); realtimeRef.current = null; };
  }, [refresh]);

  const act = async (item: IInboxItem, action: 'approve' | 'reject') => {
    setBusy((prev) => new Set(prev).add(item.task_id));
    try {
      await api.action(item.task_id, action);
      setSnapshot((prev) => (prev ? { ...prev, pending: prev.pending.filter((i) => i.task_id !== item.task_id) } : prev));
      toast.success(action === 'approve' ? `已转存「${item.title ?? '资源'}」` : '已忽略');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusy((prev) => { const next = new Set(prev); next.delete(item.task_id); return next; });
    }
  };

  const retryDeliveries = async () => {
    setRetrying(true);
    try {
      const { items } = await api.deliveries();
      let accepted = 0;
      for (const item of items) {
        try { await api.retryDelivery(item.id); accepted += 1; } catch { /* 已被其他管理端重试 */ }
      }
      toast.success(`已重新发送 ${accepted} 条通知`);
      realtimeRef.current?.flush();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '重试失败');
    } finally {
      setRetrying(false);
    }
  };

  const d = snapshot?.dashboard;
  const counts = d?.counts ?? {};
  const pendingCount = counts.pending_approval ?? 0;
  const manualCount = counts.need_manual ?? 0;
  const worker = d?.runtime.worker;
  const workerStatus = !worker ? 'not_configured' : worker.stale ? 'stale' : worker.status;
  const failedDeliveries = d?.deliveries.failed ?? 0;

  return (
    <Page
      title="总览"
      description={d ? `${modeLabels[d.mode] ?? '状态未知'} · 最近 24 小时收到 ${d.recent?.received_24h ?? 0} 条消息` : '运行状态与待办一览'}
      actions={
        <>
          <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
            mode === 'live' ? TONE_CLASS.success.badge : TONE_CLASS.neutral.badge)}>
            <span className={cn('size-1.5 rounded-full', mode === 'live' ? 'animate-pulse bg-success' : 'bg-muted-foreground/60')} />
            {LIVE_LABEL[mode]}
          </span>
          <Button variant="outline" size="sm" onClick={() => { void refresh(); }}><RefreshCw />刷新</Button>
        </>
      }
    >
      {error ? (
        <div className="mb-5 flex items-center gap-2.5 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="size-4 shrink-0" />{error}
        </div>
      ) : null}
      {d?.alerts?.map((alert) => (
        <div key={alert.key} role="alert" className="mb-5 flex items-start gap-3 rounded-xl border border-warning/35 bg-warning/8 px-4 py-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
          <div className="min-w-0 text-sm">
            <p className="font-medium text-foreground">{alert.text}</p>
            <p className="mt-0.5 text-xs text-muted-foreground" title={fullTime(alert.since)}>开始于 {relativeTime(alert.since)}</p>
          </div>
        </div>
      ))}

      {!d ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[118px] rounded-xl" />)}</div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          <StatCard label="待确认" value={pendingCount} icon={Clock3} tone="warning" to="/inbox" highlight={pendingCount > 0}
            hint={pendingCount ? '点击去审批' : '没有需要确认的资源'} />
          <StatCard label="进行中" value={d.active} icon={Loader} tone="primary" to="/tasks?group=active" hint="识别、排队、转存或整理" />
          <StatCard label="24 小时入库" value={d.recent?.done_24h ?? 0} icon={CheckCircle2} tone="success" to="/library"
            hint={`累计已入库 ${d.done}`} />
          <StatCard label="失败" value={d.failed} icon={XCircle} tone="danger" to="/tasks?group=failed" highlight={false}
            hint={manualCount ? `另有 ${manualCount} 条待人工处理` : '可在任务中重试'} />
        </div>
      )}

      <div className="mt-5 grid gap-5 lg:grid-cols-3">
        <div className="min-w-0 space-y-5 lg:col-span-2">
          <Panel
            title="等你确认"
            icon={Inbox}
            actions={pendingCount > 0 ? (
              <Link to="/inbox" className="flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                全部 {pendingCount} 条<ArrowRight className="size-3.5" />
              </Link>
            ) : null}
          >
            {!snapshot ? (
              <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>
            ) : snapshot.pending.length ? (
              <div className="divide-y divide-border/70">
                {snapshot.pending.slice(0, 4).map((item) => (
                  <PendingRow key={item.task_id} item={item} busy={busy.has(item.task_id)}
                    onOpen={() => setOpenId(item.task_id)} onAct={(action) => void act(item, action)} />
                ))}
              </div>
            ) : (
              <div className="flex items-center gap-3 py-2 text-sm text-muted-foreground">
                <span className="grid size-9 place-items-center rounded-full bg-success/10 text-success"><Check className="size-4" /></span>
                全部处理完了，新资源需要确认时会出现在这里。
              </div>
            )}
          </Panel>

          <Panel
            title="最近入库"
            icon={LibraryBig}
            actions={<Link to="/library" className="-my-2 flex items-center gap-1 py-2 text-xs font-medium text-primary hover:underline">媒体库<ArrowRight className="size-3.5" /></Link>}
          >
            {!snapshot ? (
              <div className="grid grid-cols-4 gap-3 sm:grid-cols-8">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="aspect-[2/3]" />)}</div>
            ) : snapshot.recent.length ? (
              <div className="grid grid-cols-4 gap-3 sm:grid-cols-6 xl:grid-cols-8">
                {snapshot.recent.map((item, index) => (
                  <button key={item.task_id} type="button" onClick={() => setOpenId(item.task_id)}
                    className={cn('group min-w-0 text-left', index >= 6 && 'hidden xl:block', index >= 4 && 'max-sm:hidden')}>
                    <MediaCover title={item.title} posterPath={item.poster_path} mediaType={item.media_type} quality="w185"
                      className="transition-transform group-hover:-translate-y-0.5 group-hover:shadow-md" />
                    <div className="mt-1.5 truncate text-xs font-medium">{item.title}</div>
                    <div className="truncate text-[11px] text-muted-foreground">
                      {episodeLabel(item.season, item.episodes) || item.year || ''}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <p className="py-2 text-sm text-muted-foreground">还没有入库的资源。</p>
            )}
          </Panel>
        </div>

        <div className="min-w-0 space-y-5">
          <AiSuggestions />

          <Panel title="运行状态" icon={Activity}>
            {!d ? <Skeleton className="h-48" /> : (
              <>
                <div className="divide-y divide-border/60">
                  <HealthRow label="后台 Worker" status={workerStatus}
                    detail={worker?.updated_at ? `最近心跳 ${fullTime(worker.updated_at)}` : undefined} />
                  {Object.entries(d.connections).map(([key, conn]) => (
                    <HealthRow key={key} label={conn.label} status={conn.status}
                      detail={conn.error ? `最近错误：${conn.error}` : conn.observed_at ? `更新于 ${fullTime(conn.observed_at)}` : undefined} />
                  ))}
                </div>
                {worker?.config_error ? (
                  <p className="mt-3 rounded-lg bg-destructive/5 px-3 py-2 text-xs text-destructive">{worker.config_error}</p>
                ) : worker && worker.config_revision !== d.config_revision && !worker.stale ? (
                  <p className="mt-3 rounded-lg bg-info/8 px-3 py-2 text-xs text-info">配置已保存，等待后台采用新版本…</p>
                ) : null}
              </>
            )}
          </Panel>

          <Panel title="通知发送" icon={Send}
            actions={failedDeliveries > 0 ? (
              <Button size="sm" variant="outline" disabled={retrying} onClick={() => void retryDeliveries()}>重发失败</Button>
            ) : null}
          >
            {!d ? <Skeleton className="h-16" /> : (
              <div className="grid grid-cols-3 gap-2 text-center">
                {[
                  { label: '待发送', value: (d.deliveries.pending ?? 0) + (d.deliveries.delivering ?? 0) },
                  { label: '已发送', value: d.deliveries.done ?? 0 },
                  { label: '失败', value: failedDeliveries, danger: true },
                ].map((x) => (
                  <div key={x.label} className="rounded-lg bg-muted/60 px-2 py-2.5">
                    <div className={cn('text-lg font-semibold tabular-nums', x.danger && x.value > 0 && 'text-destructive')}>{x.value}</div>
                    <div className="text-xs text-muted-foreground">{x.label}</div>
                  </div>
                ))}
              </div>
            )}
            {failedDeliveries > 0 ? <ToneBadge tone="danger" className="mt-3">有通知发送失败，可能是控制 Bot 不可用</ToneBadge> : null}
          </Panel>
        </div>
      </div>

      <TaskDetailSheet taskId={openId} onClose={() => setOpenId(null)} onChanged={() => void refresh()} />
    </Page>
  );
}
