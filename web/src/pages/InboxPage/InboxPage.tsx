import { CheckCheck, Inbox, Search, Sparkles, Wand2, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { inboxApi } from '@/api/inbox';
import { subscriptionApi } from '@/api/subscriptions';
import { taskApi } from '@/api/tasks';
import { EmptyState } from '@/components/app/EmptyState';
import { ErrorState, RefreshError } from '@/components/app/ErrorState';
import { Page } from '@/components/app/Page';
import { Segmented, SegmentCount } from '@/components/app/Segmented';
import { Select } from '@/components/app/Select';
import { TaskDetailSheet } from '@/components/TaskDetailSheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useAsync } from '@/hooks/useAsync';
import { CONFLICT_DONE, type ConflictChoice } from '@/lib/conflict';
import { Realtime } from '@/lib/realtime';
import { announceSubscription } from '@/lib/subscriptions';
import { ACTION_DONE, ACTION_ENDPOINT, actionBody, type PostedAction } from '@/lib/taskActions';
import type { IInboxItem } from '@/lib/types';
import { cn, errorMessage } from '@/lib/utils';
import {
  adviceBatches, adviceKey, advicePending, markSubscribed, visibleItems, withoutTasks,
  type InboxData, type InboxSort, type InboxTab,
} from './inbox';
import { InboxCard } from './InboxCard';

const SORTS: Array<{ value: InboxSort; label: string }> = [
  { value: 'newest', label: '最新在前' },
  { value: 'oldest', label: '最早在前' },
  { value: 'confidence', label: '置信度低的在前' },
];

const fetchInbox = (): Promise<InboxData> => Promise.all([inboxApi.list('pending_approval'), inboxApi.list('need_manual')])
  .then(([pending_approval, need_manual]) => ({ pending_approval, need_manual }));

export default function InboxPage() {
  const [tab, setTab] = useState<InboxTab>('pending_approval');
  const inbox = useAsync(fetchInbox, [], '读取待审批列表失败');
  const { reload, mutate } = inbox;
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<InboxSort>('newest');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [batchDir, setBatchDir] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    const realtime = new Realtime({ scopes: ['counts', 'tasks'], onChange: reload });
    realtime.start();
    return () => realtime.stop();
  }, [reload]);

  const data = inbox.data;
  const pending = useMemo(() => data?.pending_approval.items ?? [], [data]);

  // 规则理由先显示，再请求 AI 按同样的事实措辞（服务端按事实缓存；事实变化时理由也会变，再请求一次）。
  const phrased = useRef(new Set<string>());
  useEffect(() => {
    const ask = advicePending(pending, phrased.current);
    if (!ask.length) return;
    ask.forEach((i) => phrased.current.add(adviceKey(i)));
    inboxApi.advice(ask.map((i) => i.task_id)).then(({ items }) => {
      if (!Object.keys(items).length) return;
      mutate((prev) => prev && {
        ...prev,
        pending_approval: {
          ...prev.pending_approval,
          items: prev.pending_approval.items.map((i) => (items[i.task_id] ? { ...i, advice: items[i.task_id] } : i)),
        },
      });
    }).catch(() => undefined);  // 只影响措辞，规则理由照常显示
  }, [pending, mutate]);

  const items = useMemo(() => visibleItems(data?.[tab].items ?? [], query, sort), [data, tab, query, sort]);
  // 已不在列表里的选择不算数
  const chosen = useMemo(() => new Set(pending.filter((i) => selected.has(i.task_id)).map((i) => i.task_id)), [pending, selected]);
  const dirs = data?.pending_approval.save_dirs ?? [];
  const dirPaths = data?.pending_approval.save_dir_paths ?? {};

  const markBusy = (ids: string[], on: boolean) => setBusy((prev) => {
    const next = new Set(prev);
    for (const id of ids) if (on) next.add(id); else next.delete(id);
    return next;
  });
  const remove = (ids: string[]) => {
    mutate((prev) => prev && withoutTasks(prev, ids));
    setSelected((prev) => new Set([...prev].filter((id) => !ids.includes(id))));
  };

  const act = async (item: IInboxItem, action: PostedAction, options: { dir?: string; conflict?: ConflictChoice } = {}) => {
    markBusy([item.task_id], true);
    try {
      await taskApi.act(item.task_id, ACTION_ENDPOINT[action], actionBody(action, options));
      remove([item.task_id]);
      toast.success(options.conflict ? CONFLICT_DONE[options.conflict]
        : action === 'approve' ? `已转存「${item.title ?? '资源'}」` : ACTION_DONE[action]);
    } catch (err) {
      toast.error(errorMessage(err, '操作失败'));
    } finally {
      markBusy([item.task_id], false);
    }
  };

  /** dir 只对批准生效：统一改存到该目录，空值各自按分类存放。 */
  const batch = async (action: 'approve' | 'reject', ids: string[], dir?: string) => {
    if (!ids.length) return;
    markBusy(ids, true);
    try {
      const result = await taskApi.batch(ids, action, action === 'approve' ? dir : undefined);
      const failedIds = new Set(result.failed.map((f) => f.task_id));
      remove(ids.filter((id) => !failedIds.has(id)));
      if (result.failed.length) toast.error(`${result.done} 条成功，${result.failed.length} 条失败：${result.failed[0].error}`);
      else toast.success(action === 'approve' ? `已批准 ${result.done} 条，开始转存` : `已忽略 ${result.done} 条`);
    } catch (err) {
      toast.error(errorMessage(err, '批量操作失败'));
    } finally {
      markBusy(ids, false);
    }
  };

  const followAdvice = async () => {
    const { approve, reject } = adviceBatches(pending, chosen);
    if (!approve.length && !reject.length) { toast.info('所选条目都需要你看一下，没有可直接处理的'); return; }
    if (approve.length) await batch('approve', approve, batchDir);
    if (reject.length) await batch('reject', reject);
  };

  const subscribe = async (item: IInboxItem) => {
    markBusy([item.task_id], true);
    try {
      const { subscription, pending: same } = await subscriptionApi.addFromTask(item.task_id);
      mutate((prev) => prev && markSubscribed(prev, same));
      announceSubscription(subscription, same, remove);
    } catch (err) {
      toast.error(errorMessage(err, '订阅失败'));
    } finally {
      markBusy([item.task_id], false);
    }
  };

  const allSelected = items.length > 0 && items.every((i) => chosen.has(i.task_id));
  const counts = { pending_approval: pending.length, need_manual: data?.need_manual.items.length ?? 0 };
  const working = busy.size > 0;

  return (
    <Page
      title="待审批"
      description="AI 识别出的资源在这里等你确认；转存结果不明或整理失败的任务也会集中到「待人工」。"
    >
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
        <Segmented<InboxTab>
          label="列表" size="md" value={tab} className="shrink-0 self-start"
          onChange={(key) => { setTab(key); setSelected(new Set()); }}
          options={([['pending_approval', '待确认'], ['need_manual', '待人工']] as const).map(([value, label]) => ({
            value, label: <>{label}<SegmentCount count={counts[value]} tone="warning" /></>,
          }))}
        />
        <div className="flex flex-1 gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="按片名或来源筛选" className="h-9 bg-card pl-9 shadow-xs" />
          </div>
          <Select value={sort} onValueChange={(v) => setSort(v as InboxSort)} options={SORTS} className="w-36 shrink-0" aria-label="排序" />
        </div>
      </div>

      {data && inbox.error ? <RefreshError error={`刷新失败：${inbox.error}`} onRetry={reload} /> : null}

      {tab === 'pending_approval' && items.length > 0 ? (
        <div className={cn(
          'sticky top-14 z-20 mb-4 flex flex-wrap items-center gap-2 rounded-xl border bg-card/95 px-3 py-2 shadow-xs backdrop-blur lg:top-3',
          chosen.size > 0 && 'border-primary/40',
        )}>
          <label className="flex min-h-9 cursor-pointer items-center gap-2 px-1 text-sm sm:min-h-0">
            <input type="checkbox" checked={allSelected} className="size-4 accent-primary"
              onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((i) => i.task_id)))} />
            {chosen.size ? <span>已选 <b className="tabular-nums">{chosen.size}</b> 条</span> : <span className="text-muted-foreground">全选</span>}
          </label>
          <div className="ml-auto flex items-center gap-2">
            {dirs.length > 1 ? (
              <Select value={batchDir} onValueChange={setBatchDir} placeholder="各自按分类存放" aria-label="批量保存目录"
                options={dirs.map((d) => ({ value: d, label: `改存到「${d}」` }))} className="hidden w-40 md:block" />
            ) : null}
            {pending.some((i) => i.advice) ? (
              <Button size="sm" variant="outline" disabled={!chosen.size || working} onClick={() => void followAdvice()}
                title="所选条目按各自的建议批准或忽略；需要你看一下的保留">
                <Wand2 />按建议<span className="max-sm:hidden">处理</span>
              </Button>
            ) : null}
            <Button size="sm" variant="outline" disabled={!chosen.size || working} onClick={() => void batch('reject', [...chosen])}>
              <X />忽略<span className="max-sm:hidden">所选</span>
            </Button>
            <Button size="sm" disabled={!chosen.size || working} onClick={() => void batch('approve', [...chosen], batchDir)}>
              <CheckCheck />转存<span className="max-sm:hidden">所选</span>
            </Button>
          </div>
        </div>
      ) : null}

      {!data ? (
        inbox.error ? (
          <ErrorState title="读取待审批列表失败" error={inbox.error} onRetry={reload} retrying={inbox.loading} />
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40 rounded-xl" />)}</div>
        )
      ) : items.length === 0 ? (
        query ? (
          <EmptyState icon={Search} title="没有匹配的资源" description="换个关键词试试。" />
        ) : tab === 'pending_approval' ? (
          <EmptyState icon={Sparkles} title="没有待确认的资源" description="新识别出的资源需要你确认时会出现在这里；信任的来源可以在设置里改为 AI 处理。" />
        ) : (
          <EmptyState icon={Inbox} title="没有需要人工处理的任务" description="转存结果不明或整理失败时，任务会出现在这里等待核验。" />
        )
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {items.map((item) => (
            <InboxCard
              key={item.task_id}
              item={item}
              dirs={dirs}
              dirPaths={dirPaths}
              selected={chosen.has(item.task_id)}
              busy={busy.has(item.task_id)}
              onToggle={() => setSelected((prev) => {
                const next = new Set(prev);
                if (next.has(item.task_id)) next.delete(item.task_id); else next.add(item.task_id);
                return next;
              })}
              onOpen={() => setOpenId(item.task_id)}
              onAct={(action, options) => void act(item, action, options)}
              onSubscribe={() => void subscribe(item)}
            />
          ))}
        </div>
      )}

      <TaskDetailSheet taskId={openId} onClose={() => setOpenId(null)} onChanged={reload}
        saveDirs={data ? { dirs, paths: dirPaths } : undefined} />
    </Page>
  );
}
