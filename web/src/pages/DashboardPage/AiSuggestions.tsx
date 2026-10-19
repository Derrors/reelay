import { ArrowRight, BellPlus, Sparkles, TrendingUp } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { ai, type ILlmUsage, type ISourceInsight, type ISubscriptionSuggestion } from '@/api/ai';
import { extras } from '@/api/extras';
import { Panel } from '@/components/app/Panel';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';

interface State {
  auto: ISourceInsight[];
  subscriptions: ISubscriptionSuggestion[];
  usage: ILlmUsage | null;
}

const message = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

/** 总览的 AI 建议：可以改为自动转存的来源、值得订阅的剧，以及近 30 天的 AI 调用概况。建议都要你点了才生效。 */
export function AiSuggestions() {
  const [state, setState] = useState<State | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => Promise.all([
    ai.insights().catch(() => null), ai.subscriptions().catch(() => null), ai.usage(30).catch(() => null),
  ]).then(([insights, subs, usage]) => setState({
    auto: insights?.sources.filter((s) => s.suggest_auto) ?? [],
    subscriptions: subs?.suggestions.slice(0, 3) ?? [],
    usage,
  })), []);
  useEffect(() => { void load(); }, [load]);

  const enableAuto = async (source: string) => {
    setBusy(true);
    try {
      await ai.enableAuto(source);
      toast.success(`「${source}」已改为自动转存；拿不准的资源仍会交给你确认`);
      await load();
    } catch (e) {
      toast.error(message(e, '保存失败'));
    } finally {
      setBusy(false);
    }
  };

  const subscribe = async (s: ISubscriptionSuggestion) => {
    setBusy(true);
    try {
      const r = await ai.subscribe({ title: s.title, season: s.season, tmdbid: s.tmdbid });
      toast.success(`已订阅《${s.title}》第 ${s.season} 季`, r.pending.length ? {
        description: `待审批里还有 ${r.pending.length} 条属于这部剧`,
        action: { label: `一并转存 ${r.pending.length} 条`, onClick: () => { void extras.batch(r.pending, 'approve'); } },
        duration: 10000,
      } : undefined);
      await load();
    } catch (e) {
      toast.error(message(e, '订阅失败'));
    } finally {
      setBusy(false);
    }
  };

  const usage = state?.usage?.total;
  const empty = state && !state.auto.length && !state.subscriptions.length;
  return (
    <Panel title="AI 建议" icon={Sparkles}>
      {!state ? <Skeleton className="h-20" /> : (
        <div className="space-y-3">
          {state.auto.map((s) => (
            <div key={s.source} className="rounded-lg border border-success/25 bg-success/5 p-3">
              <p className="flex items-center gap-1.5 text-[13px] font-medium"><TrendingUp className="size-3.5 text-success" />{s.source}</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{s.reason}。拿不准的资源仍会交给你。</p>
              <Button size="sm" variant="outline" className="mt-2 bg-card" disabled={busy} onClick={() => void enableAuto(s.source)}>
                改为自动转存
              </Button>
            </div>
          ))}
          {state.subscriptions.map((s) => (
            <div key={`${s.tmdbid ?? s.title}-${s.season}`} className="rounded-lg border border-border bg-background/60 p-3">
              <p className="text-xs leading-5">{s.text}</p>
              <Button size="sm" variant="outline" className="mt-2" disabled={busy} onClick={() => void subscribe(s)}>
                <BellPlus />订阅
              </Button>
            </div>
          ))}
          {empty ? <p className="text-xs leading-5 text-muted-foreground">暂时没有建议。你批准、忽略与修正得越多，建议越准。</p> : null}
          {usage ? (
            <Link to="/stats" className="flex items-center justify-between gap-2 border-t border-border/70 pt-3 text-xs text-muted-foreground hover:text-primary">
              <span>
                近 30 天 AI 调用 {usage.calls} 次
                {usage.invalid ? `，未通过校验 ${usage.invalid} 次` : ''}
                {usage.prompt_tokens + usage.completion_tokens ? `，约 ${Math.round((usage.prompt_tokens + usage.completion_tokens) / 1000)} 千 token` : ''}
              </span>
              <ArrowRight className="size-3.5 shrink-0" />
            </Link>
          ) : null}
        </div>
      )}
    </Panel>
  );
}
