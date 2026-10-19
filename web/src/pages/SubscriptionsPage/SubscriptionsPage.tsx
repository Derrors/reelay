import { BellPlus, BellRing, CircleAlert, Lightbulb, Loader2, Search, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { ai, type IResolveResp, type IShowCandidate, type ISubscription, type ISubscriptionsResp } from '@/api/ai';
import { extras } from '@/api/extras';
import { EmptyState } from '@/components/app/EmptyState';
import { MediaCover } from '@/components/app/MediaCover';
import { Page } from '@/components/app/Page';
import { Panel } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { compactEpisodes } from '@/lib/format';

const message = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

/** 订阅后待审批里同一部剧的条目可以一并转存。 */
function announce(title: string, season: number, pending: string[]) {
  toast.success(`已订阅《${title}》第 ${season} 季，之后的新集会自动转存`, pending.length ? {
    description: `待审批里还有 ${pending.length} 条属于这部剧`,
    action: {
      label: `一并转存 ${pending.length} 条`,
      onClick: () => {
        extras.batch(pending, 'approve')
          .then((r) => toast.success(`已批准 ${r.done} 条，开始转存`))
          .catch((e: unknown) => toast.error(message(e, '批量转存失败')));
      },
    },
    duration: 10000,
  } : undefined);
}

function CandidateCard({ candidate, wanted, onSubscribe, busy }: {
  candidate: IShowCandidate; wanted: number | null; busy: boolean;
  onSubscribe: (season: number, total: number | null) => void;
}) {
  const seasons = candidate.seasons;
  const initial = seasons.some((s) => s.season === wanted) ? wanted : seasons.at(-1)?.season ?? wanted ?? 1;
  const [season, setSeason] = useState(String(initial));
  const picked = seasons.find((s) => String(s.season) === season);
  return (
    <div className="flex gap-3 rounded-lg border border-border bg-background/60 p-3">
      <MediaCover title={candidate.title} posterPath={candidate.poster_path} mediaType="tv" quality="w185" className="w-14 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col">
        <p className="truncate text-sm font-medium">
          {candidate.title}
          {candidate.year ? <span className="ml-1.5 text-xs font-normal text-muted-foreground">{candidate.year}</span> : null}
        </p>
        {candidate.original_title && candidate.original_title !== candidate.title ? (
          <p className="truncate text-xs text-muted-foreground">{candidate.original_title}</p>
        ) : null}
        {candidate.overview ? <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{candidate.overview}</p> : null}
        <div className="mt-auto flex items-center gap-2 pt-2">
          {seasons.length ? (
            <Select value={season} onValueChange={setSeason} aria-label="选择季" className="w-36"
              options={seasons.map((s) => ({ value: String(s.season), label: `第 ${s.season} 季${s.episodes ? ` · ${s.episodes} 集` : ''}` }))} />
          ) : null}
          <Button size="sm" disabled={busy} className="ml-auto"
            onClick={() => onSubscribe(Number(season), picked?.episodes ?? null)}>
            <BellPlus />订阅
          </Button>
        </div>
      </div>
    </div>
  );
}

function SubscriptionRow({ item, busy, onRemove }: { item: ISubscription; busy: boolean; onRemove: () => void }) {
  return (
    <div className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
      <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><BellRing className="size-4" /></span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-sm font-medium">《{item.title}》第 {item.season} 季</span>
          {item.auto ? <ToneBadge tone="success">新集自动转存</ToneBadge> : <ToneBadge tone="neutral">只提醒缺集</ToneBadge>}
          {!item.tmdbid ? <ToneBadge tone="warning" className="max-sm:hidden">按片名匹配</ToneBadge> : null}
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          已入库 {item.have.length}{item.total ? ` / ${item.total}` : ''} 集
          {item.have.length ? `（${compactEpisodes(item.have)}）` : ''}
          {item.missing.length ? ` · 缺 ${compactEpisodes(item.missing)}` : ''}
          {item.aired ? ` · TMDB 已播 ${item.aired} 集` : ''}
        </p>
      </div>
      <Button variant="ghost" size="icon" disabled={busy} onClick={onRemove} aria-label={`取消订阅《${item.title}》`} title="取消订阅"
        className="size-8 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
        <Trash2 />
      </Button>
    </div>
  );
}

export default function SubscriptionsPage() {
  const [data, setData] = useState<ISubscriptionsResp | null>(null);
  const [loadError, setLoadError] = useState('');
  const [query, setQuery] = useState('');
  const [resolved, setResolved] = useState<IResolveResp | null>(null);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => ai.subscriptions()
    .then((r) => { setData(r); setLoadError(''); })
    .catch((e: unknown) => setLoadError(message(e, '读取订阅失败'))), []);
  useEffect(() => { void load(); }, [load]);

  const search = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      setResolved(await ai.resolveShow(query.trim()));
    } catch (e) {
      toast.error(message(e, '查找失败'));
    } finally {
      setSearching(false);
    }
  };

  const subscribe = async (item: { title: string; season: number; tmdbid?: number | null; total?: number | null }) => {
    setBusy(true);
    try {
      const r = await ai.subscribe(item);
      announce(r.subscription.title, r.subscription.season, r.pending);
      setResolved(null);
      setQuery('');
      await load();
    } catch (e) {
      toast.error(message(e, '订阅失败'));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (item: ISubscription) => {
    setBusy(true);
    try {
      await ai.unsubscribe({ title: item.title, season: item.season, tmdbid: item.tmdbid });
      toast.success(`已取消订阅《${item.title}》第 ${item.season} 季`);
      await load();
    } catch (e) {
      toast.error(message(e, '取消订阅失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page
      title="追剧订阅"
      description="订阅的剧之后的新集直接转存，不再逐条等你确认；过滤规则、置信度、TMDB 匹配与分享清单校验照常生效。每日缺集巡检也会检查订阅的剧。"
    >
      {loadError ? (
        <EmptyState icon={CircleAlert} title="订阅读取失败" description={loadError}
          action={<Button variant="outline" onClick={() => void load()}>重试</Button>} />
      ) : null}

      {data?.inactive.length ? (
        <div role="alert" className="mb-5 flex items-start gap-3 rounded-xl border border-warning/35 bg-warning/8 px-4 py-3 text-sm">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
          <p className="min-w-0 leading-6">
            「{data.inactive.join('」「')}」的转存判断方式是「规则判断」，订阅的新集仍会等你确认。
            可以在 <Link to="/extensions" className="text-primary hover:underline">插件 → 核心能力</Link> 改为「规则 + 订阅放行」。
          </p>
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-5 lg:items-start">
        <div className="min-w-0 space-y-5 lg:col-span-3">
          <Panel icon={Search} title="添加订阅" description="用一句话说在追哪部剧，AI 帮你对应到 TMDB 条目和季。">
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void search(); }}>
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="如：我在追《一瓯春》第二季"
                className="bg-card" aria-label="在追的剧" />
              <Button type="submit" disabled={searching || !query.trim()}>
                {searching ? <Loader2 className="animate-spin" /> : <Search />}查找
              </Button>
            </form>
            {resolved ? (
              <div className="mt-4 space-y-3">
                {resolved.candidates.length ? resolved.candidates.map((c) => (
                  <CandidateCard key={c.tmdbid} candidate={c} wanted={resolved.query.season} busy={busy}
                    onSubscribe={(season, total) => void subscribe({ title: c.title, season, tmdbid: c.tmdbid, total })} />
                )) : (
                  <div className="rounded-lg border border-dashed border-border p-3 text-sm">
                    <p className="text-muted-foreground">
                      {resolved.tmdb ? '在 TMDB 上没有找到匹配的剧。' : '未配置 TMDB，无法查找条目。'}
                      可以按片名订阅，资源识别出的规范片名与它一致时才会自动转存。
                    </p>
                    {resolved.query.title ? (
                      <Button size="sm" variant="outline" className="mt-2.5" disabled={busy}
                        onClick={() => void subscribe({ title: resolved.query.title, season: resolved.query.season ?? 1 })}>
                        <BellPlus />按片名订阅《{resolved.query.title}》第 {resolved.query.season ?? 1} 季
                      </Button>
                    ) : null}
                  </div>
                )}
              </div>
            ) : null}
          </Panel>

          <Panel icon={BellRing} title="已订阅"
            description={data ? `共 ${data.items.length} 部` : undefined}>
            {!data ? <div className="space-y-3">{[0, 1].map((i) => <Skeleton key={i} className="h-12" />)}</div>
              : data.items.length ? (
                <div className="divide-y divide-border/70">
                  {data.items.map((item) => (
                    <SubscriptionRow key={`${item.tmdbid ?? item.title}-${item.season}`} item={item} busy={busy}
                      onRemove={() => void remove(item)} />
                  ))}
                </div>
              ) : <p className="text-sm text-muted-foreground">还没有订阅。在待审批里点「追这部剧」，或在上面用一句话添加。</p>}
          </Panel>
        </div>

        <Panel icon={Lightbulb} title="订阅建议" className="lg:col-span-2"
          description="同一部剧同一季批准过至少 3 条、还没订阅的剧。">
          {!data ? <Skeleton className="h-20" /> : data.suggestions.length ? (
            <div className="space-y-3">
              {data.suggestions.map((s) => (
                <div key={`${s.tmdbid ?? s.title}-${s.season}`} className="rounded-lg border border-border bg-background/60 p-3">
                  <p className="text-sm leading-6">{s.text}</p>
                  <Button size="sm" className="mt-2" disabled={busy}
                    onClick={() => void subscribe({ title: s.title, season: s.season, tmdbid: s.tmdbid })}>
                    <BellPlus />订阅第 {s.season} 季
                  </Button>
                </div>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">暂时没有。批准过几集同一部剧后，这里会提示你订阅。</p>}
        </Panel>
      </div>
    </Page>
  );
}
