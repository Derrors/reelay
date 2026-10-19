import { AlertTriangle, Check, CheckCheck, FolderTree, Inbox, Search, ShieldCheck, Sparkles, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { extras } from '@/api/extras';
import { EmptyState } from '@/components/app/EmptyState';
import { MediaCover } from '@/components/app/MediaCover';
import { MediaMeta } from '@/components/app/MediaMeta';
import { Page } from '@/components/app/Page';
import { Select } from '@/components/app/Select';
import { ToneBadge } from '@/components/app/StatusBadge';
import { TaskDetailSheet } from '@/components/TaskDetailSheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { fullTime, relativeTime } from '@/lib/format';
import { Realtime } from '@/lib/realtime';
import { CONFIDENCE_CAP_LABELS, type Tone } from '@/lib/status';
import type { IInboxItem, IShareProbe } from '@/lib/types';
import { cn } from '@/lib/utils';

type Tab = 'pending_approval' | 'need_manual';
type Sort = 'newest' | 'oldest' | 'confidence';

const SORTS: Array<{ value: Sort; label: string }> = [
  { value: 'newest', label: '最新在前' },
  { value: 'oldest', label: '最早在前' },
  { value: 'confidence', label: '置信度低的在前' },
];

type InboxData = Awaited<ReturnType<typeof fetchInbox>>;

function fetchInbox() {
  return Promise.all([api.inbox('pending_approval'), api.inbox('need_manual')]);
}

function reportLoadError(err: unknown) {
  toast.error(err instanceof Error ? err.message : '读取待审批列表失败');
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.8 ? 'bg-success' : value >= 0.5 ? 'bg-primary' : 'bg-warning';
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" title="由标题、类型、年份与 TMDB 匹配等信号计算">
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-muted">
        <span className={cn('block h-full rounded-full', tone)} style={{ width: `${Math.max(pct, 4)}%` }} />
      </span>
      <span className="tabular-nums">{pct}%</span>
    </span>
  );
}

/** 消息没写集数的剧集读取分享的结果：集数来自分享时注明，失效、多季等转存时会转人工的情况提前标出。 */
function shareBadge(share: IShareProbe | null): { tone: Tone; label: string; title: string } | null {
  if (!share?.status) return null;
  const note = share.note ?? '';
  switch (share.status) {
    case 'ok':
      return share.from_share ? { tone: 'info', label: '集数来自分享', title: `读取分享：${share.videos ?? 0} 个视频` } : null;
    case 'unavailable':
      return { tone: 'danger', label: '分享无法访问', title: note };
    case 'issue':
      return { tone: 'warning', label: note.split(/[：，]/)[0] || '转存时需人工', title: `${note}（转存时会转人工）` };
    case 'incomplete':
      return { tone: 'neutral', label: '分享内容较多，未读完', title: note };
    default:
      return null;  // 读取出错：启动时会重试，不打扰
  }
}

interface CardProps {
  item: IInboxItem;
  dirs: string[];
  /** 目录别名 → 网盘真实路径，用于在选项里注明位置。 */
  dirPaths: Record<string, string>;
  selected: boolean;
  busy: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onAct: (action: 'approve' | 'reject' | 'reconcile' | 'reorganize', dir?: string) => void;
}

function InboxCard({ item, dirs, dirPaths, selected, busy, onToggle, onOpen, onAct }: CardProps) {
  const [dir, setDir] = useState('');  // 空值 = 按自动分类存放
  const manual = item.state === 'need_manual';
  const autoTarget = item.auto_path ?? item.auto_dir ?? '按分类';
  const share = shareBadge(item.share);
  return (
    <article className={cn(
      'group relative flex gap-4 rounded-xl border border-card-border bg-card p-4 shadow-xs transition-shadow hover:shadow-md',
      selected && 'border-primary/50 ring-1 ring-primary/30',
    )}>
      <button type="button" onClick={onOpen} className="w-14 shrink-0 self-start sm:w-[84px]" aria-label={`查看 ${item.title ?? '任务'} 详情`}>
        <MediaCover title={item.title} posterPath={item.poster_path} mediaType={item.media_type} quality="w185" />
      </button>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-start gap-2">
          <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left">
            <h3 className="truncate text-[15px] font-semibold leading-6 hover:text-primary">
              {item.title || '尚未识别片名'}
              {item.year ? <span className="ml-1.5 text-sm font-normal text-muted-foreground">{item.year}</span> : null}
            </h3>
          </button>
          {!manual ? (
            <input
              type="checkbox"
              checked={selected}
              onChange={onToggle}
              aria-label="选择"
              className="mt-1 size-4 shrink-0 cursor-pointer rounded accent-primary"
            />
          ) : null}
        </div>
        <MediaMeta mediaType={item.media_type} season={item.season} episodes={item.episodes} pairs={item.episode_pairs}
          resolution={item.resolution} size={item.size} className="mt-0.5" />
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="min-w-0 truncate">{item.source} · <span title={fullTime(item.created_at)}>{relativeTime(item.created_at)}</span></span>
          {!manual ? <ConfidenceBar value={item.confidence} /> : null}
        </div>
        {!manual && (item.fallback || item.confidence_caps.length || share) ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {share ? <span title={share.title}><ToneBadge tone={share.tone}>{share.label}</ToneBadge></span> : null}
            {item.fallback ? <ToneBadge tone="warning">AI 识别降级</ToneBadge> : null}
            {item.confidence_caps.filter((c) => c !== 'parse_fallback').map((cap) => (
              <ToneBadge key={cap} tone="warning">{CONFIDENCE_CAP_LABELS[cap] ?? cap}</ToneBadge>
            ))}
          </div>
        ) : null}
        {manual ? (
          <div className="mt-2 flex gap-2 rounded-lg bg-destructive/5 px-2.5 py-2 text-xs leading-5 text-destructive">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <span className="line-clamp-3 break-words">{item.organize_error || item.error || '需要人工确认处理结果'}</span>
          </div>
        ) : item.preview ? (
          <p className="mt-2 line-clamp-2 whitespace-pre-line border-l-2 border-border pl-2 text-xs leading-5 text-muted-foreground">{item.preview}</p>
        ) : null}

        <div className="mt-auto flex items-center justify-end gap-2 pt-3">
          {!manual ? (
            <>
              {dirs.length > 1 ? (
                <Select value={dir} onValueChange={setDir} aria-label="保存目录" className="mr-auto min-w-0 max-w-56 flex-1"
                  title={dir && dir !== item.auto_dir?.split('/')[0] ? `直接存入「${dirPaths[dir] ?? dir}」，不套用分类子目录`
                    : `按分类存入「${autoTarget}」`}
                  placeholder={`自动 · ${autoTarget}`}
                  options={dirs.map((d) => ({ value: d, label: dirPaths[d] ? `${d}（${dirPaths[d]}）` : d }))} />
              ) : null}
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAct('reject')} aria-label="忽略"
                className="shrink-0 px-2 text-muted-foreground hover:text-destructive sm:px-3">
                <X /><span className="max-sm:hidden">忽略</span>
              </Button>
              <Button size="sm" disabled={busy} onClick={() => onAct('approve', dir || undefined)} className="shrink-0"><Check />转存</Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="ghost" onClick={onOpen}>详情</Button>
              {item.storage_phase === 'receiving' ? (
                <Button size="sm" variant="outline" disabled={busy} onClick={() => onAct('reconcile')}><ShieldCheck />核验转存</Button>
              ) : null}
              {item.has_landed ? (
                <Button size="sm" variant="outline" disabled={busy} onClick={() => onAct('reorganize')}><FolderTree />重新整理</Button>
              ) : null}
            </>
          )}
        </div>
      </div>
    </article>
  );
}

export default function InboxPage() {
  const [tab, setTab] = useState<Tab>('pending_approval');
  const [data, setData] = useState<Record<Tab, IInboxItem[] | null>>({ pending_approval: null, need_manual: null });
  const [dirs, setDirs] = useState<string[]>([]);
  const [dirPaths, setDirPaths] = useState<Record<string, string>>({});
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<Sort>('newest');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [batchDir, setBatchDir] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);

  const apply = useCallback(([pending, manual]: InboxData) => {
    setData({ pending_approval: pending.items, need_manual: manual.items });
    setDirs(pending.save_dirs);
    setDirPaths(pending.save_dir_paths ?? {});
    // 已不在列表中的选择自动清除
    const ids = new Set(pending.items.map((i) => i.task_id));
    setSelected((prev) => new Set([...prev].filter((id) => ids.has(id))));
  }, []);

  const load = useCallback(() => fetchInbox().then(apply).catch(reportLoadError), [apply]);

  useEffect(() => {
    let active = true;
    fetchInbox().then((result) => { if (active) apply(result); }).catch(reportLoadError);
    const realtime = new Realtime({ scopes: ['counts', 'tasks'], onChange: () => { void load(); } });
    realtime.start();
    return () => { active = false; realtime.stop(); };
  }, [apply, load]);

  const items = useMemo(() => {
    const list = [...(data[tab] ?? [])];
    const q = query.trim().toLowerCase();
    const filtered = q ? list.filter((i) => `${i.title ?? ''} ${i.source ?? ''}`.toLowerCase().includes(q)) : list;
    if (sort === 'oldest') filtered.sort((a, b) => a.created_at - b.created_at);
    else if (sort === 'confidence') filtered.sort((a, b) => a.confidence - b.confidence);
    else filtered.sort((a, b) => b.created_at - a.created_at);
    return filtered;
  }, [data, tab, query, sort]);

  const remove = (ids: string[]) => {
    setData((prev) => ({ ...prev, [tab]: (prev[tab] ?? []).filter((i) => !ids.includes(i.task_id)) }));
    setSelected((prev) => new Set([...prev].filter((id) => !ids.includes(id))));
  };

  const act = async (item: IInboxItem, action: 'approve' | 'reject' | 'reconcile' | 'reorganize', dir?: string) => {
    setBusy((prev) => new Set(prev).add(item.task_id));
    try {
      const body = action === 'approve' && dir ? { save_dir_alias: dir } : action === 'reorganize' ? {} : undefined;
      await api.action(item.task_id, action, body);
      remove([item.task_id]);
      toast.success({ approve: `已转存「${item.title ?? '资源'}」`, reject: '已忽略', reconcile: '已排队核验', reorganize: '已排队重新整理' }[action]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusy((prev) => { const next = new Set(prev); next.delete(item.task_id); return next; });
    }
  };

  const batch = async (action: 'approve' | 'reject') => {
    const ids = [...selected];
    if (!ids.length) return;
    setBusy(new Set(ids));
    try {
      const result = await extras.batch(ids, action, action === 'approve' ? batchDir || undefined : undefined);
      const failedIds = new Set(result.failed.map((f) => f.task_id));
      remove(ids.filter((id) => !failedIds.has(id)));
      if (result.failed.length) toast.error(`${result.done} 条成功，${result.failed.length} 条失败：${result.failed[0].error}`);
      else toast.success(action === 'approve' ? `已批准 ${result.done} 条，开始转存` : `已忽略 ${result.done} 条`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量操作失败');
    } finally {
      setBusy(new Set());
    }
  };

  const loading = data[tab] === null;
  const allSelected = items.length > 0 && items.every((i) => selected.has(i.task_id));
  const counts = { pending_approval: data.pending_approval?.length ?? 0, need_manual: data.need_manual?.length ?? 0 };

  return (
    <Page
      title="待审批"
      description="AI 识别出的资源在这里等你确认；转存结果不明或整理失败的任务也会集中到「待人工」。"
    >
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
        <div role="tablist" className="inline-flex shrink-0 rounded-lg bg-muted p-1">
          {([['pending_approval', '待确认'], ['need_manual', '待人工']] as const).map(([key, label]) => (
            <button
              key={key}
              role="tab"
              type="button"
              aria-selected={tab === key}
              onClick={() => { setTab(key); setSelected(new Set()); }}
              className={cn(
                'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                tab === key ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {label}
              <span className={cn('rounded-full px-1.5 text-[11px] tabular-nums', counts[key] ? 'bg-warning/15 text-warning' : 'bg-background text-muted-foreground')}>
                {counts[key]}
              </span>
            </button>
          ))}
        </div>
        <div className="flex flex-1 gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="按片名或来源筛选" className="h-9 bg-card pl-9 shadow-xs" />
          </div>
          <Select value={sort} onValueChange={(v) => setSort(v as Sort)} options={SORTS} className="w-36 shrink-0" aria-label="排序" />
        </div>
      </div>

      {tab === 'pending_approval' && items.length > 0 ? (
        <div className={cn(
          'sticky top-14 z-20 mb-4 flex flex-wrap items-center gap-2 rounded-xl border bg-card/95 px-3 py-2 shadow-xs backdrop-blur lg:top-3',
          selected.size > 0 && 'border-primary/40',
        )}>
          <label className="flex cursor-pointer items-center gap-2 px-1 text-sm">
            <input type="checkbox" checked={allSelected} className="size-4 accent-primary"
              onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((i) => i.task_id)))} />
            {selected.size ? <span>已选 <b className="tabular-nums">{selected.size}</b> 条</span> : <span className="text-muted-foreground">全选</span>}
          </label>
          <div className="ml-auto flex items-center gap-2">
            {dirs.length > 1 ? (
              <Select value={batchDir} onValueChange={setBatchDir} placeholder="各自按分类存放" aria-label="批量保存目录"
                options={dirs.map((d) => ({ value: d, label: `改存到「${d}」` }))} className="hidden w-40 md:block" />
            ) : null}
            <Button size="sm" variant="outline" disabled={!selected.size || busy.size > 0} onClick={() => void batch('reject')}>
              <X />忽略<span className="max-sm:hidden">所选</span>
            </Button>
            <Button size="sm" disabled={!selected.size || busy.size > 0} onClick={() => void batch('approve')}>
              <CheckCheck />转存<span className="max-sm:hidden">所选</span>
            </Button>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="grid gap-4 xl:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40 rounded-xl" />)}</div>
      ) : items.length === 0 ? (
        query ? (
          <EmptyState icon={Search} title="没有匹配的资源" description="换个关键词试试。" />
        ) : tab === 'pending_approval' ? (
          <EmptyState icon={Sparkles} title="没有待确认的资源" description="新识别出的资源需要你确认时会出现在这里；信任的来源可以在设置里改为自动转存。" />
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
              selected={selected.has(item.task_id)}
              busy={busy.has(item.task_id)}
              onToggle={() => setSelected((prev) => {
                const next = new Set(prev);
                if (next.has(item.task_id)) next.delete(item.task_id); else next.add(item.task_id);
                return next;
              })}
              onOpen={() => setOpenId(item.task_id)}
              onAct={(action, dir) => void act(item, action, dir)}
            />
          ))}
        </div>
      )}

      <TaskDetailSheet taskId={openId} onClose={() => setOpenId(null)} onChanged={() => void load()} />
    </Page>
  );
}
