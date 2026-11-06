import { Check, ChevronRight, EyeOff, Link2Off, ListChecks, MessageSquareText, RefreshCw, RotateCcw, ScanSearch, Search, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';
import { taskApi } from '@/api/tasks';
import { EmptyState } from '@/components/app/EmptyState';
import { ErrorState, RefreshError } from '@/components/app/ErrorState';
import { MediaCover } from '@/components/app/MediaCover';
import { MediaMeta } from '@/components/app/MediaMeta';
import { Page } from '@/components/app/Page';
import { Segmented, SegmentCount } from '@/components/app/Segmented';
import { StatusBadge } from '@/components/app/StatusBadge';
import { TaskDetailSheet } from '@/components/TaskDetailSheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useAsync } from '@/hooks/useAsync';
import { useDebounced } from '@/hooks/useDebounced';
import { useDashboard } from '@/hooks/useDashboard';
import { confirmDialog } from '@/lib/confirm';
import { groupByDay } from '@/lib/dayGroups';
import { fullTime, relativeTime } from '@/lib/format';
import { Realtime } from '@/lib/realtime';
import { STATE_GROUPS, TONE_CLASS, type Tone } from '@/lib/status';
import type { ITaskSummary, TaskState } from '@/lib/types';
import { cn, errorMessage } from '@/lib/utils';

const PAGE_SIZE = 50;
/** 列表接口单次最多 500 条：实时刷新时按已加载的条数重取，超过的部分等用户再点「加载更多」。 */
const MAX_LIMIT = 500;

/** 分享已失效（取消、过期）的失败任务：来源不会再修好这条链接，只能忽略。 */
const isShareGone = (item: ITaskSummary) => item.state === 'failed' && (item.error ?? '').startsWith('分享不可用');

interface ListResult {
  items: ITaskSummary[];
  hasMore: boolean;
}

function fetchTasks(states: TaskState[], query: string, limit: number, offset = 0) {
  return taskApi.list({ state: states.join(','), query, limit, offset }).then(({ items }) => items);
}

function TaskRow({ item, onOpen, selecting, selected, onSelect }: {
  item: ITaskSummary; onOpen: () => void;
  selecting: boolean; selected: boolean; onSelect: (range: boolean) => void;
}) {
  const unrecognized = !item.title;
  const title = item.title || (item.state === 'rejected' || item.state === 'skipped' ? '未识别为影视资源' : '尚未识别片名');
  return (
    <button
      type="button"
      onClick={(e) => (selecting ? onSelect(e.shiftKey) : onOpen())}
      aria-pressed={selecting ? selected : undefined}
      className={cn('group flex w-full items-center gap-3 px-4 py-3 text-left transition-colors focus-visible:bg-muted/60 focus-visible:outline-none',
        selected ? 'bg-primary/6 hover:bg-primary/9' : 'hover:bg-muted/50')}
    >
      <span className={cn('grid shrink-0 place-items-center overflow-hidden transition-[width,opacity] duration-300 ease-(--ease-out-strong)',
        selecting ? 'w-4 opacity-100' : '-mr-3 w-0 opacity-0')} aria-hidden>
        <span className={cn('grid size-4 place-items-center rounded-[4px] border-[1.5px] transition-colors',
          selected ? 'border-primary bg-primary text-primary-foreground' : 'border-faint bg-card')}>
          {selected ? <Check className="size-3" strokeWidth={3} /> : null}
        </span>
      </span>
      {unrecognized ? (
        <span className="grid aspect-[2/3] w-9 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
          <MessageSquareText className="size-4" />
        </span>
      ) : (
        <MediaCover title={item.title} posterPath={item.poster_path} mediaType={item.media_type} quality="w185" className="w-9 shrink-0 rounded-md" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className={cn('truncate text-sm font-medium', unrecognized && 'text-muted-foreground')}>{title}</span>
          {item.year ? <span className="shrink-0 text-xs text-muted-foreground">{item.year}</span> : null}
        </div>
        <MediaMeta mediaType={item.media_type} season={item.season} episodes={item.episodes} pairs={item.episode_pairs}
          resolution={item.resolution} size={item.size} className="mt-0.5" />
        {item.error ? <p className="mt-0.5 truncate text-xs text-destructive">{item.error}</p> : null}
        <p className="mt-0.5 truncate text-xs text-muted-foreground md:hidden">
          {item.source} · {clock(item.updated_at)}
        </p>
      </div>
      <span className="hidden w-44 shrink-0 truncate text-xs text-muted-foreground md:block">{item.source}</span>
      <span className="hidden w-16 shrink-0 truncate text-xs text-muted-foreground lg:block">{item.save_dir_alias ?? '—'}</span>
      <span className="flex w-[72px] shrink-0 justify-end"><StatusBadge state={item.state} /></span>
      <span className="hidden w-20 shrink-0 text-right text-xs tabular-nums text-muted-foreground md:block" title={fullTime(item.updated_at)}>
        {relativeTime(item.updated_at)}
      </span>
      <ChevronRight className={cn('size-4 shrink-0 text-faint transition-[color,transform] duration-300 ease-(--ease-out-strong) group-hover:translate-x-0.5 group-hover:text-foreground',
        selecting && 'invisible')} />
    </button>
  );
}

const CLOCK = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' });
/** 列表已按日期分组，行里只写时刻。 */
const clock = (seconds: number) => CLOCK.format(new Date(seconds * 1000));

/** 批量操作：按钮、提示与后端动作。 */
const BULK = [
  { action: 'reparse', label: '重新识别', icon: ScanSearch, done: '已重新识别' },
  { action: 'retry', label: '重试', icon: RotateCcw, done: '已重新排队' },
  { action: 'reject', label: '忽略', icon: EyeOff, done: '已忽略' },
] as const;

/** 状态筛选前的色点，与状态标签同色；「全部」没有。 */
const GROUP_TONES: Record<string, Tone | undefined> = {
  pending: 'warning', active: 'primary', manual: 'warning', failed: 'danger', done: 'success', filtered: 'neutral',
};

export default function TasksPage() {
  const [params, setParams] = useSearchParams();
  const group = STATE_GROUPS.find((g) => g.key === params.get('group')) ?? STATE_GROUPS[0];
  const [query, setQuery] = useState(() => params.get('q') ?? ''); // 命令搜索（⌘K）带过来的片名
  const submitted = useDebounced(query);
  const [loadingMore, setLoadingMore] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<{ key: string; ids: Set<string>; anchor: string | null }>({ key: '', ids: new Set(), anchor: null });
  const [bulkBusy, setBulkBusy] = useState(false);
  const counts = useDashboard().dashboard?.counts ?? {};

  // 已加载的页数：换了筛选回到第一页；实时刷新时按已加载的条数重取，不打断阅读
  const key = `${group.key}|${submitted}`;
  const [pages, setPages] = useState({ key, count: 1 });
  if (pages.key !== key) setPages({ key, count: 1 });
  const list = useAsync<ListResult>(() => {
    const limit = Math.min(MAX_LIMIT, (pages.key === key ? pages.count : 1) * PAGE_SIZE);
    return fetchTasks(group.states, submitted, limit).then((items) => ({ items, hasMore: items.length === limit }));
  }, [group.key, submitted], '读取任务失败');
  const { reload, mutate } = list;

  useEffect(() => {
    const realtime = new Realtime({ scopes: ['tasks'], onChange: reload });
    realtime.start();
    return () => realtime.stop();
  }, [reload]);

  const result = list.stale ? undefined : list.data;
  const loadMore = async () => {
    if (!result) return;
    setLoadingMore(true);
    try {
      const page = await fetchTasks(group.states, submitted, PAGE_SIZE, result.items.length);
      mutate((prev) => prev && {
        items: [...prev.items, ...page.filter((i) => !prev.items.some((p) => p.task_id === i.task_id))],
        hasMore: page.length === PAGE_SIZE,
      });
      setPages((prev) => ({ key, count: prev.count + 1 }));
    } catch (err) {
      toast.error(errorMessage(err, '加载失败'));
    } finally {
      setLoadingMore(false);
    }
  };

  // 换了筛选或搜索就清空选择
  const chosen = selected.key === key ? selected.ids : new Set<string>();
  const toggle = (id: string, range: boolean) => setSelected((prev) => {
    const ids = new Set(prev.key === key ? prev.ids : []);
    const anchor = prev.key === key ? prev.anchor : null;
    const order = (result?.items ?? []).map((i) => i.task_id);
    if (range && anchor && order.includes(anchor)) {
      // Shift 点选：从上次点的那条到这条全部选上
      const [a, b] = [order.indexOf(anchor), order.indexOf(id)].sort((x, y) => x - y);
      order.slice(a, b + 1).forEach((t) => ids.add(t));
    } else if (ids.has(id)) ids.delete(id);
    else ids.add(id);
    return { key, ids, anchor: id };
  });
  const toggleMany = (ids: string[]) => setSelected((prev) => {
    const next = new Set(prev.key === key ? prev.ids : []);
    const all = ids.every((id) => next.has(id));
    ids.forEach((id) => (all ? next.delete(id) : next.add(id)));
    return { key, ids: next, anchor: null };
  });
  const stopSelecting = () => { setSelecting(false); setSelected({ key: '', ids: new Set(), anchor: null }); };
  const runBulk = async (action: typeof BULK[number]['action']) => {
    const meta = BULK.find((b) => b.action === action)!;
    if (action === 'reject' && !(await confirmDialog({
      title: `忽略选中的 ${chosen.size} 条任务？`, description: '待确认、失败或待人工的任务会转为已忽略，不再处理；其他状态的会跳过。',
      confirmText: '忽略', destructive: true,
    }))) return;
    setBulkBusy(true);
    try {
      const r = await taskApi.batch([...chosen], action);
      const skipped = r.failed.length;
      if (r.done) toast.success(`${meta.done} ${r.done} 条${skipped ? `，${skipped} 条不适用（${r.failed[0].error}）` : ''}`);
      else toast.error(`没有可以${meta.label}的任务${skipped ? `：${r.failed[0].error}` : ''}`);
      stopSelecting();
      reload();
    } catch (err) {
      toast.error(errorMessage(err, `批量${meta.label}失败`));
    } finally {
      setBulkBusy(false);
    }
  };

  const countOf = (states: TaskState[]) => (states.length ? states : (Object.keys(counts) as TaskState[]))
    .reduce((sum, s) => sum + (counts[s] ?? 0), 0);
  const items = result?.items ?? [];
  const shareGone = items.filter(isShareGone);
  const [ignoring, setIgnoring] = useState(false);
  const ignoreShareGone = async () => {
    setIgnoring(true);
    try {
      const r = await taskApi.batch(shareGone.map((i) => i.task_id), 'reject');
      if (r.failed.length) toast.error(`${r.failed.length} 条没能忽略：${r.failed[0].error}`);
      if (r.done) toast.success(`已忽略 ${r.done} 条分享失效的任务`);
      reload();
    } catch (err) {
      toast.error(errorMessage(err, '忽略失败'));
    } finally {
      setIgnoring(false);
    }
  };

  return (
    <Page
      title="任务"
      description="每条消息从接收到入库的完整记录。点开任务可查看识别结果、处理过程，并修正、重试或重新整理。"
      actions={(
        <>
          <Button variant={selecting ? 'secondary' : 'outline'} size="sm" onClick={() => (selecting ? stopSelecting() : setSelecting(true))}>
            {selecting ? <><X />完成</> : <><ListChecks />批量操作</>}
          </Button>
          <Button variant="outline" size="sm" onClick={reload}><RefreshCw />刷新</Button>
        </>
      )}
    >
      {result && list.error ? <RefreshError error={`刷新失败：${list.error}`} onRetry={reload} /> : null}
      <div className="mb-4 flex flex-col gap-3 xl:flex-row xl:items-center">
        {/* 窄屏横向滑动，不换成好几行 */}
        <div className="-mx-4 overflow-x-auto px-4 [scrollbar-width:none] sm:mx-0 sm:px-0">
        <Segmented
          variant="plain" size="md" label="按状态筛选" value={group.key} className="flex-nowrap sm:flex-wrap"
          onChange={(key) => setParams(key === 'all' ? {} : { group: key }, { replace: true })}
          options={STATE_GROUPS.map((g) => {
            const count = countOf(g.states);
            const tone = GROUP_TONES[g.key];
            return {
              value: g.key,
              label: (
                <>
                  {tone ? <span className={cn('size-1.5 rounded-full', TONE_CLASS[tone].dot)} /> : null}
                  {g.label}
                  <SegmentCount count={count} tone={g.key === 'failed' ? 'danger' : g.key === 'pending' || g.key === 'manual' ? 'warning' : undefined} />
                </>
              ),
            };
          })}
        />
        </div>
        <div className="relative xl:ml-auto xl:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索片名、来源或错误信息" className="pl-9" />
        </div>
      </div>

      {group.key === 'failed' && shareGone.length ? (
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl glass-panel px-4 py-2.5 text-[13px] shadow-card">
          <Link2Off className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1">
            <b className="font-semibold tabular-nums">{shareGone.length}</b> 条任务的分享已取消或过期，无法再转存
          </span>
          <Button size="sm" variant="outline" disabled={ignoring} onClick={() => void ignoreShareGone()}>
            <EyeOff />全部忽略
          </Button>
        </div>
      ) : null}

      <div className="overflow-clip rounded-xl glass-panel shadow-card">
        <div className="hidden items-center gap-3 border-b bg-muted/40 px-4 py-2 text-xs font-medium text-muted-foreground md:flex">
          <span className="w-9 shrink-0" />
          <span className="flex-1">资源</span>
          <span className="w-44 shrink-0">来源</span>
          <span className="hidden w-16 shrink-0 lg:block">目录</span>
          <span className="w-[72px] shrink-0 text-right">状态</span>
          <span className="w-20 shrink-0 text-right">更新</span>
          <span className="w-4 shrink-0" />
        </div>
        {!result ? (
          list.error ? (
            <ErrorState title="读取任务失败" error={list.error} onRetry={reload} retrying={list.loading} className="m-4 border-0" />
          ) : (
            <div className="space-y-px p-2">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
          )
        ) : items.length === 0 ? (
          <EmptyState
            icon={submitted ? Search : ListChecks}
            title={submitted ? '没有匹配的任务' : '这里还没有任务'}
            description={submitted ? '换个关键词或状态试试。' : '监听到新消息后，任务会自动出现在这里。'}
            className="m-4 border-0"
          />
        ) : (
          <div>
            {groupByDay(items, (i) => i.updated_at).map((day) => {
              const ids = day.items.map((i) => i.task_id);
              const all = ids.every((id) => chosen.has(id));
              return (
                <section key={day.label} className="border-t first:border-t-0">
                  <div className="sticky top-[calc(3.5rem+env(safe-area-inset-top))] z-10 flex items-center gap-2 border-b glass-card glass-blur px-4 py-1.5 text-xs lg:top-0">
                    {selecting ? (
                      <button type="button" onClick={() => toggleMany(ids)} className="press font-medium text-primary">
                        {all ? '取消' : '全选'}
                      </button>
                    ) : null}
                    <span className="font-medium text-foreground/80">{day.label}</span>
                    <span className="tabular-nums text-faint">{day.items.length} 条</span>
                  </div>
                  <div className="divide-y divide-border/70">
                    {day.items.map((item) => (
                      <TaskRow key={item.task_id} item={item} onOpen={() => setOpenId(item.task_id)}
                        selecting={selecting} selected={chosen.has(item.task_id)} onSelect={(range) => toggle(item.task_id, range)} />
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </div>

      {result?.hasMore ? (
        <div className="mt-4 flex justify-center">
          <Button variant="outline" size="sm" disabled={loadingMore} onClick={() => void loadMore()}>
            {loadingMore ? '加载中…' : '加载更多'}
          </Button>
        </div>
      ) : null}

      {/* 批量操作条：选中后从底部浮起（手机上在底部标签栏上方） */}
      <div className={cn('pointer-events-none fixed inset-x-0 bottom-[calc(5.5rem+env(safe-area-inset-bottom))] z-30 flex justify-center px-4 transition-[opacity,transform] duration-300 ease-(--ease-out-strong) lg:bottom-6 lg:pl-(--sidebar-width)',
        selecting && chosen.size ? 'translate-y-0 opacity-100' : 'translate-y-3 opacity-0')}>
        <div className="pointer-events-auto flex items-center gap-1 rounded-xl glass-card glass-blur p-1.5 pl-3.5 shadow-overlay">
          <span className="mr-1 text-[13px] tabular-nums">已选 <b className="font-semibold">{chosen.size}</b> 条</span>
          {BULK.map(({ action, label, icon: Icon }) => (
            <Button key={action} size="sm" variant={action === 'reject' ? 'ghost' : 'outline'} disabled={bulkBusy || !chosen.size}
              onClick={() => void runBulk(action)} className={action === 'reject' ? 'text-destructive hover:text-destructive' : undefined}>
              <Icon />{label}
            </Button>
          ))}
        </div>
      </div>

      <TaskDetailSheet taskId={openId} onClose={() => setOpenId(null)} onChanged={reload} />
    </Page>
  );
}
