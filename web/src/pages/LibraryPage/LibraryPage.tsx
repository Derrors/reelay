import { FolderTree, LayoutGrid, LibraryBig, List, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { extras, type ITitleGroup } from '@/api/extras';
import { EmptyState } from '@/components/app/EmptyState';
import { MediaCover } from '@/components/app/MediaCover';
import { Page } from '@/components/app/Page';
import { Select } from '@/components/app/Select';
import { Sheet } from '@/components/app/Sheet';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { compactEpisodes, fullTime, mediaTypeLabel, relativeTime, seasonsLabel } from '@/lib/format';
import { cn } from '@/lib/utils';

type Sort = 'recent' | 'title' | 'size';
type View = 'grid' | 'list';

const TYPES = [
  { value: '', label: '全部' },
  { value: 'tv', label: '剧集' },
  { value: 'movie', label: '电影' },
  { value: 'anime', label: '动漫' },
];
const SORTS: Array<{ value: Sort; label: string }> = [
  { value: 'recent', label: '最近入库' },
  { value: 'title', label: '按片名' },
  { value: 'size', label: '按体积' },
];

interface LibraryResult {
  key: string;
  groups: ITitleGroup[];
  gb: number;
}

function gbText(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  return gb >= 100 ? `${gb.toFixed(0)} GB` : `${gb.toFixed(1)} GB`;
}

/** 已收录集数：跨多季时按（季, 集）计，否则各季的同号集会被当成一集。 */
function episodeCount(group: ITitleGroup): number {
  return group.episode_pairs?.length ?? group.episodes.length;
}

/** 按季归并已收录的集；单季作品只有一组（季为 null）。 */
function seasonGroups(group: ITitleGroup): Array<[number | null, number[]]> {
  if (!group.episode_pairs) return [[null, group.episodes]];
  const bySeason = new Map<number, number[]>();
  for (const [season, ep] of group.episode_pairs) bySeason.set(season, [...(bySeason.get(season) ?? []), ep]);
  return [...bySeason].sort((a, b) => a[0] - b[0]).map(([season, eps]) => [season, eps.sort((a, b) => a - b)]);
}

/** 海报角标：剧集显示已收录集数，电影不显示。 */
function summary(group: ITitleGroup): string {
  if (group.media_type === 'movie') return group.year ? String(group.year) : '电影';
  const eps = episodeCount(group);
  return [group.year, eps ? `${eps} 集` : ''].filter(Boolean).join(' · ');
}

function GroupSheet({ group, onClose }: { group: ITitleGroup | null; onClose: () => void }) {
  return (
    <Sheet
      open={group !== null}
      onOpenChange={(open) => { if (!open) onClose(); }}
      title={group?.title ?? ''}
      description={group ? `${mediaTypeLabel(group.media_type)}${group.year ? ` · ${group.year}` : ''}` : undefined}
    >
      {group ? (
        <div className="space-y-6">
          <div className="flex gap-4">
            <MediaCover title={group.title} posterPath={group.poster_path} mediaType={group.media_type} className="w-28 shrink-0" />
            <dl className="grid content-start gap-x-4 gap-y-2 text-sm [grid-template-columns:auto_1fr]">
              <dt className="text-muted-foreground">保存目录</dt><dd>{group.save_dir_alias ?? '—'}</dd>
              <dt className="text-muted-foreground">估算体积</dt><dd className="tabular-nums">{gbText(group.bytes)}</dd>
              <dt className="text-muted-foreground">转存次数</dt><dd className="tabular-nums">{group.tasks}</dd>
              <dt className="text-muted-foreground">最近入库</dt>
              <dd title={fullTime(group.updated_at)}>{relativeTime(group.updated_at)}</dd>
            </dl>
          </div>
          {group.media_type !== 'movie' && group.episodes.length ? (
            <section className="space-y-2">
              <h3 className="text-[13px] font-semibold">已收录 {episodeCount(group)} 集</h3>
              {seasonGroups(group).map(([season, eps]) => (
                <div key={season ?? 'all'} className="flex items-start gap-2">
                  {season !== null ? (
                    <span className="w-9 shrink-0 pt-0.5 text-xs font-medium tabular-nums text-muted-foreground">S{String(season).padStart(2, '0')}</span>
                  ) : null}
                  <div className="flex flex-wrap gap-1.5">
                    {eps.map((ep) => (
                      <span key={ep} className="min-w-9 rounded-md bg-primary/10 px-1.5 py-0.5 text-center text-xs font-medium tabular-nums text-primary">
                        {String(ep).padStart(2, '0')}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </section>
          ) : null}
          <section className="space-y-2">
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold"><FolderTree className="size-3.5 text-muted-foreground" />文件</h3>
            {group.files.length ? (
              <ul className="space-y-1 rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-5">
                {group.files.map((path) => {
                  const cut = path.lastIndexOf('/');
                  return (
                    <li key={path} className="break-all">
                      {cut > 0 ? <span className="text-muted-foreground">{path.slice(0, cut + 1)}</span> : null}
                      {path.slice(cut + 1)}
                    </li>
                  );
                })}
              </ul>
            ) : <p className="text-xs text-muted-foreground">没有整理记录。</p>}
          </section>
        </div>
      ) : null}
    </Sheet>
  );
}

export default function LibraryPage() {
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [type, setType] = useState('');
  const [sort, setSort] = useState<Sort>('recent');
  const [view, setView] = useState<View>(() => (localStorage.getItem('reelay-library-view') as View) || 'grid');
  const [result, setResult] = useState<LibraryResult | null>(null);
  const [open, setOpen] = useState<ITitleGroup | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setSubmitted(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const key = `${type}|${submitted}`;
  useEffect(() => {
    let active = true;
    extras.advancedTasks(submitted || undefined, type || undefined)
      .then((r) => { if (active) setResult({ key, groups: r.groups, gb: r.estimated_gb }); })
      .catch(() => { if (active) setResult({ key, groups: [], gb: 0 }); });
    return () => { active = false; };
  }, [key, submitted, type]);

  const groups = useMemo(() => {
    const list = [...(result?.groups ?? [])];
    if (sort === 'title') list.sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'));
    else if (sort === 'size') list.sort((a, b) => b.bytes - a.bytes);
    else list.sort((a, b) => b.updated_at - a.updated_at);
    return list;
  }, [result, sort]);

  const loading = result?.key !== key;
  const switchView = (next: View) => { setView(next); localStorage.setItem('reelay-library-view', next); };

  return (
    <Page
      title="媒体库"
      description="已入库的影视作品，按片名汇总。"
      actions={result && !loading ? (
        <span className="text-sm text-muted-foreground">
          <b className="font-semibold tabular-nums text-foreground">{result.groups.length}</b> 部 ·
          约 <b className="font-semibold tabular-nums text-foreground">{result.gb}</b> GB
        </span>
      ) : null}
    >
      <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center">
        <div role="radiogroup" aria-label="类型" className="inline-flex shrink-0 self-start rounded-lg bg-muted p-1">
          {TYPES.map((t) => (
            <button
              key={t.value || 'all'}
              type="button"
              role="radio"
              aria-checked={type === t.value}
              onClick={() => setType(t.value)}
              className={cn('rounded-md px-3 py-1.5 text-sm font-medium transition-colors sm:py-1',
                type === t.value ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground')}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex flex-1 gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索片名" className="h-9 bg-card pl-9 shadow-xs" />
          </div>
          <Select value={sort} onValueChange={(v) => setSort(v as Sort)} options={SORTS} aria-label="排序" className="w-28 shrink-0" />
          <div className="hidden shrink-0 rounded-lg bg-muted p-1 sm:inline-flex">
            {([['grid', LayoutGrid, '海报墙'], ['list', List, '列表']] as const).map(([value, Icon, label]) => (
              <button key={value} type="button" title={label} aria-pressed={view === value} onClick={() => switchView(value)}
                className={cn('grid size-7 place-items-center rounded-md text-muted-foreground',
                  view === value && 'bg-card text-foreground shadow-xs')}>
                <Icon className="size-4" /><span className="sr-only">{label}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6">
          {Array.from({ length: 12 }, (_, i) => <Skeleton key={i} className="aspect-[2/3] rounded-lg" />)}
        </div>
      ) : groups.length === 0 ? (
        <EmptyState
          icon={submitted || type ? Search : LibraryBig}
          title={submitted || type ? '没有匹配的作品' : '媒体库还是空的'}
          description={submitted || type ? '换个关键词或类型试试。' : '资源转存并整理完成后，会按片名汇总到这里。'}
        />
      ) : view === 'grid' ? (
        <div className="grid grid-cols-3 gap-x-3 gap-y-5 sm:grid-cols-4 sm:gap-x-4 md:grid-cols-5 lg:grid-cols-6">
          {groups.map((g) => (
            <button key={g.title} type="button" onClick={() => setOpen(g)} className="group min-w-0 text-left">
              <MediaCover title={g.title} posterPath={g.poster_path} mediaType={g.media_type}
                className="transition-all duration-200 group-hover:-translate-y-1 group-hover:shadow-lg" />
              <div className="mt-2 truncate text-[13px] font-medium group-hover:text-primary">{g.title}</div>
              <div className="truncate text-xs text-muted-foreground">{summary(g)}</div>
            </button>
          ))}
        </div>
      ) : (
        <div className="divide-y divide-border/70 overflow-hidden rounded-xl border border-card-border bg-card shadow-xs">
          {groups.map((g) => (
            <button key={g.title} type="button" onClick={() => setOpen(g)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50">
              <MediaCover title={g.title} posterPath={g.poster_path} mediaType={g.media_type} quality="w185" className="w-9 shrink-0 rounded-md" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">
                  {g.title}{g.year ? <span className="ml-1.5 text-xs font-normal text-muted-foreground">{g.year}</span> : null}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {mediaTypeLabel(g.media_type)}
                  {g.media_type !== 'movie' && g.episodes.length
                    ? ` · ${g.episode_pairs ? seasonsLabel(g.episode_pairs) : `${g.episodes.length} 集：${compactEpisodes(g.episodes, 4)}`}` : ''}
                </div>
              </div>
              <span className="hidden w-16 shrink-0 text-xs text-muted-foreground sm:block">{g.save_dir_alias ?? ''}</span>
              <span className="w-16 shrink-0 text-right text-xs tabular-nums text-muted-foreground">{gbText(g.bytes)}</span>
            </button>
          ))}
        </div>
      )}

      <GroupSheet group={open} onClose={() => setOpen(null)} />
    </Page>
  );
}
