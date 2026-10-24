import { ArrowRight, BellPlus, Sparkles, TrendingUp } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { aiApi, type ILlmUsage } from '@/api/ai';
import { sourceApi, type ISourceInsight } from '@/api/sources';
import { subscriptionApi, type ISubscriptionSuggestion } from '@/api/subscriptions';
import { Panel } from '@/components/app/Panel';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useAsync } from '@/hooks/useAsync';
import { announceSubscription } from '@/lib/subscriptions';
import { errorMessage } from '@/lib/utils';

interface State {
  auto: ISourceInsight[];
  subscriptions: ISubscriptionSuggestion[];
  usage: ILlmUsage | null;
}

/** 三项各自失败都不影响其他：只是少一类建议。 */
const fetchSuggestions = (): Promise<State> => Promise.all([
  sourceApi.insights().catch(() => null), subscriptionApi.list().catch(() => null), aiApi.usage(30).catch(() => null),
]).then(([insights, subs, usage]) => ({
  auto: insights?.sources.filter((s) => s.suggest_auto) ?? [],
  subscriptions: subs?.suggestions.slice(0, 3) ?? [],
  usage,
}));

/** 总览的 AI 建议：可以改为 AI 处理的来源、值得订阅的剧，以及近 30 天的 AI 调用概况。建议都要你点了才生效。 */
export function AiSuggestions() {
  const { data: state, reload } = useAsync(fetchSuggestions, []);
  const [busy, setBusy] = useState(false);

  const enableAuto = async (source: string) => {
    setBusy(true);
    try {
      await sourceApi.enableAuto(source);
      toast.success(`「${source}」已改为 AI 处理；拿不准的资源仍会交给你确认`);
      reload();
    } catch (e) {
      toast.error(errorMessage(e, '保存失败'));
    } finally {
      setBusy(false);
    }
  };

  const subscribe = async (s: ISubscriptionSuggestion) => {
    setBusy(true);
    try {
      const r = await subscriptionApi.add({ title: s.title, season: s.season, tmdbid: s.tmdbid });
      announceSubscription(s, r.pending);
      reload();
    } catch (e) {
      toast.error(errorMessage(e, '订阅失败'));
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
                改为 AI 处理
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
            <Link to="/stats?tab=ai" className="flex items-center justify-between gap-2 border-t border-border/70 pt-3 text-xs text-muted-foreground hover:text-primary">
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
