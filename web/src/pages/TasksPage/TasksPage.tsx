import { ChevronRight, EyeOff, Link2Off, ListChecks, MessageSquareText, RefreshCw, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';
import { taskApi } from '@/api/tasks';
import { EmptyState } from '@/components/app/EmptyState';
import { ErrorState, RefreshError } from '@/components/app/ErrorState';
import { MediaCover } from '@/components/app/MediaCover';
import { MediaMeta } from '@/components/app/MediaMeta';
import { Page } from '@/components/app/Page';
import { StatusBadge } from '@/components/app/StatusBadge';
import { TaskDetailSheet } from '@/components/TaskDetailSheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useAsync } from '@/hooks/useAsync';
import { useDebounced } from '@/hooks/useDebounced';
import { useDashboard } from '@/hooks/useDashboard';
import { fullTime, relativeTime } from '@/lib/format';
import { Realtime } from '@/lib/realtime';
import { STATE_GROUPS } from '@/lib/status';
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

function TaskRow({ item, onOpen }: { item: ITaskSummary; onOpen: () => void }) {
  const unrecognized = !item.title;
  const title = item.title || (item.state === 'rejected' || item.state === 'skipped' ? '未识别为影视资源' : '尚未识别片名');
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/60 focus-visible:outline-none"
    >
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
          {item.source} · {relativeTime(item.updated_at)}
        </p>
      </div>
      <span className="hidden w-44 shrink-0 truncate text-xs text-muted-foreground md:block">{item.source}</span>
      <span className="hidden w-16 shrink-0 truncate text-xs text-muted-foreground lg:block">{item.save_dir_alias ?? '—'}</span>
      <span className="flex w-[72px] shrink-0 justify-end"><StatusBadge state={item.state} /></span>
      <span className="hidden w-20 shrink-0 text-right text-xs tabular-nums text-muted-foreground md:block" title={fullTime(item.updated_at)}>
        {relativeTime(item.updated_at)}
      </span>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground/40 transition-colors group-hover:text-foreground" />
    </button>
  );
}

export default function TasksPage() {
  const [params, setParams] = useSearchParams();
  const group = STATE_GROUPS.find((g) => g.key === params.get('group')) ?? STATE_GROUPS[0];
  const [query, setQuery] = useState('');
  const submitted = useDebounced(query);
  const [loadingMore, setLoadingMore] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
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
      actions={<Button variant="outline" size="sm" onClick={reload}><RefreshCw />刷新</Button>}
    >
      {result && list.error ? <RefreshError error={`刷新失败：${list.error}`} onRetry={reload} /> : null}
      <div className="mb-4 flex flex-col gap-3 xl:flex-row xl:items-center">
        <div className="flex flex-wrap gap-1.5">
          {STATE_GROUPS.map((g) => {
            const active = g.key === group.key;
            const count = countOf(g.states);
            return (
              <button
                key={g.key}
                type="button"
                aria-pressed={active}
                onClick={() => setParams(g.key === 'all' ? {} : { group: g.key }, { replace: true })}
                className={cn(
                  'flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-medium transition-colors sm:min-h-0 sm:py-1',
                  active ? 'border-primary/40 bg-primary/10 text-primary' : 'border-border bg-card text-muted-foreground hover:text-foreground',
                )}
              >
                {g.label}
                <span className={cn('text-xs tabular-nums', active ? 'text-primary/80' : 'text-muted-foreground/70',
                  count > 0 && (g.key === 'failed' ? 'text-destructive' : g.key === 'pending' || g.key === 'manual' ? 'text-warning' : ''))}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>
        <div className="relative xl:ml-auto xl:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索片名、来源或错误信息" className="h-9 bg-card pl-9 shadow-xs" />
        </div>
      </div>

      {group.key === 'failed' && shareGone.length ? (
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-border bg-card px-4 py-2.5 text-[13px] shadow-xs">
          <Link2Off className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1">
            <b className="font-semibold tabular-nums">{shareGone.length}</b> 条任务的分享已取消或过期，无法再转存
          </span>
          <Button size="sm" variant="outline" disabled={ignoring} onClick={() => void ignoreShareGone()}>
            <EyeOff />全部忽略
          </Button>
        </div>
      ) : null}

      <div className="overflow-hidden rounded-xl border border-card-border bg-card shadow-xs">
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
          <div className="divide-y divide-border/70">
            {items.map((item) => <TaskRow key={item.task_id} item={item} onOpen={() => setOpenId(item.task_id)} />)}
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

      <TaskDetailSheet taskId={openId} onClose={() => setOpenId(null)} onChanged={reload} />
    </Page>
  );
}
