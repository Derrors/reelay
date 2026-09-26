import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, CircleDashed, RefreshCw } from 'lucide-react';
import { api } from '@/api/client';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import type { IDashboard } from '@/lib/types';
import { connectionLabels } from '@/lib/status';

export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<IDashboard | null>(null);
  const [error, setError] = useState('');
  const [retryingDeliveries, setRetryingDeliveries] = useState(false);

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
      toast.info(`${accepted} 条投递已重新入队${accepted < items.length ? '，其余项目未入队，请刷新检查' : ''}`);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '投递重试失败');
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
    const timer = window.setInterval(refresh, 10000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const cards = dashboard ? [
    { label: '运行中', value: dashboard.active, note: '待解析、排队与转存中', tone: 'text-info' },
    { label: '待人工', value: dashboard.pending, note: '待审批或待整理', tone: 'text-warning' },
    { label: '失败', value: dashboard.failed, note: '可进入任务中心重试', tone: 'text-destructive' },
    { label: '已完成', value: dashboard.done, note: '已转存并验证', tone: 'text-success' },
  ] : [];

  return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader
        eyebrow="运行总览"
        title="任务流水线正在发生什么"
        description="查看任务进展、监听连接、后台处理和消息投递状态。连接状态来自运行时观察，未验证的凭证会单独标记。"
        action={<Button variant="outline" onClick={() => { void load().then((ok) => { if (ok) toast.success('状态已刷新'); }); }}><RefreshCw className="size-4" />刷新</Button>}
      />

      {error ? (
        <div className="mt-6 flex items-center gap-3 border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <AlertTriangle className="size-5" />{error}
        </div>
      ) : null}

      {!dashboard ? (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[1, 2, 3, 4].map((item) => <Skeleton key={item} className="h-32 rounded-md" />)}
        </div>
      ) : (
        <>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {cards.map((card) => (
              <Card key={card.label} className="rounded-md shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm font-medium text-muted-foreground">{card.label}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className={`font-mono text-4xl font-semibold ${card.tone}`}>{card.value}</div>
                  <p className="mt-2 text-xs text-muted-foreground">{card.note}</p>
                </CardContent>
              </Card>
            ))}
          </div>

          <Card className="mt-6 rounded-md shadow-none">
            <CardHeader><CardTitle className="text-base">后台运行与投递</CardTitle></CardHeader>
            <CardContent className="grid gap-4 text-sm sm:grid-cols-3">
              <div>
                <p className="text-muted-foreground">后台处理</p>
                <p className="mt-1">{dashboard.runtime.worker?.stale ? '心跳已过期' : dashboard.runtime.worker?.status === 'running' ? '运行中' : '未运行'}</p>
                {dashboard.runtime.worker?.updated_at ? <p className="mt-1 text-xs text-muted-foreground">上次心跳 {new Date(dashboard.runtime.worker.updated_at * 1000).toLocaleString()}</p> : null}
              </div>
              <div>
                <p className="text-muted-foreground">配置版本</p>
                <p className="mt-1 font-mono text-xs">{dashboard.config_revision}</p>
                <p className="mt-1 text-xs text-muted-foreground">{dashboard.runtime.worker?.config_revision === dashboard.config_revision ? '后台已采用此版本' : '等待后台采用此版本'}</p>
                {dashboard.runtime.worker?.config_error ? <p className="mt-1 text-xs text-destructive">{dashboard.runtime.worker.config_error}</p> : null}
              </div>
              <div>
                <p className="text-muted-foreground">通知与事件投递</p>
                <p className="mt-1">待投递 {dashboard.deliveries.pending ?? 0} · 投递中 {dashboard.deliveries.delivering ?? 0}</p>
                <p className={`mt-1 text-xs ${(dashboard.deliveries.failed ?? 0) > 0 ? 'text-destructive' : 'text-muted-foreground'}`}>失败 {dashboard.deliveries.failed ?? 0} · 已处理 {dashboard.deliveries.done ?? 0} · 已忽略 {dashboard.deliveries.skipped ?? 0}</p>
                {(dashboard.deliveries.failed ?? 0) > 0 ? <Button className="mt-2" size="sm" variant="outline" disabled={retryingDeliveries} onClick={() => { void retryDeliveries(); }}>重试失败投递</Button> : null}
              </div>
            </CardContent>
          </Card>

          <div className="mt-6 grid gap-6 xl:grid-cols-[1.2fr_1fr]">
            <Card className="rounded-md shadow-none">
              <CardHeader>
                <CardTitle className="text-base">处理链路</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-0">
                  {[
                    ['收到消息', dashboard.counts.received ?? 0, '监听器只去重并落库'],
                    ['AI 解析', dashboard.counts.parsed ?? 0, 'Worker 异步识别与决策'],
                    ['人工闸门', dashboard.counts.pending_approval ?? 0, '确认后进入转存队列'],
                    ['转存整理', (dashboard.counts.queued ?? 0) + (dashboard.counts.saving ?? 0), '115 执行与读回验证'],
                    ['完成', dashboard.counts.done ?? 0, '文件已落地'],
                  ].map(([label, value, note], index) => (
                    <div key={String(label)} className="grid grid-cols-[24px_110px_52px_minmax(0,1fr)] items-center gap-3 border-b py-3 last:border-b-0">
                      <div className="flex size-6 items-center justify-center rounded-full border border-primary/30 bg-primary/5 font-mono text-[10px] text-primary">{index + 1}</div>
                      <div className="text-sm font-medium">{label}</div>
                      <div className="font-mono text-sm text-primary">{value}</div>
                      <div className="truncate text-xs text-muted-foreground">{note}</div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card className="rounded-md shadow-none">
              <CardHeader>
                <CardTitle className="flex items-center justify-between text-base">
                  连接状态
                  <span className="text-xs font-normal text-muted-foreground">{{ simulation: '模拟模式', live: '运行中', not_configured: '待配置', not_running: '待启动' }[dashboard.mode]}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {Object.entries(dashboard.connections).map(([key, conn]) => (
                  <div key={key} className="flex items-center justify-between border-b pb-3 last:border-b-0 last:pb-0">
                    <span className="text-sm">{conn.label}</span>
                    <span className={`flex items-center gap-1.5 text-xs ${conn.status === 'connected' ? 'text-success' : 'text-muted-foreground'}`}>
                      {conn.status === 'connected' ? <CheckCircle2 className="size-4" /> : <CircleDashed className="size-4" />}
                      {connectionLabels[conn.status] ?? '待验证'}
                    </span>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
