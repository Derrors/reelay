import {
  CircleCheck, CircleX, ListFilter, LoaderCircle, MessageSquareText, Play, ScanSearch,
} from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { extras, type ISourcesResp } from '@/api/extras';
import { EmptyState } from '@/components/app/EmptyState';
import { MediaCover } from '@/components/app/MediaCover';
import { MediaMeta } from '@/components/app/MediaMeta';
import { Page } from '@/components/app/Page';
import { Panel } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { approvalShortLabels, CONFIDENCE_CAP_LABELS, decisionLabels, TONE_CLASS, type Tone } from '@/lib/status';
import type { IParsedResource } from '@/lib/types';
import { cn } from '@/lib/utils';

const SAMPLE = '📺 示例剧名 (2026) S01E01 ✨4K WEB-DL\n💾 大小：1.2 GB\n🔗 链接：https://115cdn.com/s/swabc123?password=x1y2';

/** /api/sources/test-parse 的完整返回；extras.ts 里的类型只声明了前两项。 */
interface IConfidence {
  score: number;
  checks?: Record<string, boolean>;
  caps?: string[];
  note?: string;
}
interface ITestParseResult {
  parsed: IParsedResource | null;
  decision: string;
  confidence: IConfidence | null;
  trail?: string[];
}

const DECISION_META: Record<string, { tone: Tone; hint: string }> = {
  save: { tone: 'success', hint: '符合当前规则，会直接进入转存。' },
  ask: { tone: 'warning', hint: '会进入「待审批」，等你确认后再转存。' },
  skip: { tone: 'neutral', hint: '不符合当前规则，会被忽略，不会转存。' },
};

const CHECK_LABELS: Record<string, string> = {
  title: '片名',
  type: '类型与集数',
  year: '年份',
  tmdb: 'TMDB 匹配',
};

function Field({ label, htmlFor, hint, extra, children }: {
  label: string; htmlFor: string; hint?: ReactNode; extra?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex min-h-5 items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-[13px] font-medium">{label}</label>
        {extra}
      </div>
      {children}
      {hint ? <p className="text-xs leading-5 text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn('border-t border-border/70 pt-4', className)}>
      <h3 className="mb-2.5 text-xs font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function ConfidenceBlock({ confidence }: { confidence: IConfidence }) {
  const pct = Math.round(confidence.score * 100);
  const tone: Tone = confidence.score >= 0.8 ? 'success' : confidence.score >= 0.5 ? 'primary' : 'warning';
  const checks = Object.entries(confidence.checks ?? {});
  const caps = confidence.caps ?? [];
  return (
    <Section title="置信度">
      <div className="flex items-center gap-3">
        <span className={cn('text-2xl font-semibold leading-none', TONE_CLASS[tone].text)}>
          {pct}<span className="ml-0.5 text-sm font-medium">%</span>
        </span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
          <div className={cn('h-full rounded-full', TONE_CLASS[tone].dot)} style={{ width: `${Math.max(pct, 2)}%` }} />
        </div>
      </div>
      {checks.length ? (
        <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
          {checks.map(([key, ok]) => (
            <li key={key} className="flex items-center gap-1.5 text-xs">
              {ok
                ? <CircleCheck className="size-3.5 shrink-0 text-success" />
                : <CircleX className="size-3.5 shrink-0 text-destructive" />}
              <span className={ok ? 'text-foreground/80' : 'text-muted-foreground'}>{CHECK_LABELS[key] ?? key}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {caps.length ? (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">分数被封顶：</span>
          {caps.map((cap) => <ToneBadge key={cap} tone="warning">{CONFIDENCE_CAP_LABELS[cap] ?? cap}</ToneBadge>)}
        </div>
      ) : null}
      {confidence.note ? <p className="mt-2 text-xs text-muted-foreground">{confidence.note}</p> : null}
    </Section>
  );
}

function Trail({ entries }: { entries: string[] }) {
  return (
    <Section title="处理记录">
      <ol className="relative space-y-2.5 before:absolute before:bottom-1.5 before:left-[3px] before:top-1.5 before:w-px before:bg-border">
        {entries.map((entry, i) => {
          const match = /^(\d{1,2}:\d{2}:\d{2})\s+(.*)$/.exec(entry);
          return (
            <li key={i} className="relative flex gap-3 pl-4 text-xs leading-5">
              <span className="absolute left-0 top-[7px] size-[7px] rounded-full bg-primary/70 ring-2 ring-card" />
              {match ? <span className="shrink-0 font-mono text-[11px] text-muted-foreground tabular-nums">{match[1]}</span> : null}
              <span className="min-w-0 break-words text-foreground/85">{match ? match[2] : entry}</span>
            </li>
          );
        })}
      </ol>
    </Section>
  );
}

function ResultView({ result }: { result: ITestParseResult }) {
  const p = result.parsed;
  const posterPath = typeof p?.raw_fields?.poster_path === 'string' ? p.raw_fields.poster_path : null;
  const hasLink = Boolean(p?.share_code);

  return (
    <div className="space-y-4">
      {p?.is_media ? (
        <div className="flex gap-4">
          <MediaCover title={p.title} posterPath={posterPath} mediaType={p.media_type} quality="w185" className="w-[72px] shrink-0 self-start sm:w-20" />
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold leading-7">
              {p.title || <span className="text-muted-foreground">未能识别片名</span>}
              {p.year ? <span className="ml-2 text-sm font-normal text-muted-foreground">{p.year}</span> : null}
            </h3>
            <MediaMeta
              mediaType={p.media_type}
              season={p.season}
              episodes={p.episodes}
              resolution={p.resolution}
              size={p.size}
              className="mt-1 text-[13px]"
            />
            <div className="mt-3 flex flex-wrap gap-1.5">
              {hasLink
                ? <ToneBadge tone="success">已提取 115 链接</ToneBadge>
                : <ToneBadge tone="warning">没有找到 115 链接</ToneBadge>}
              {p.is_ad ? <ToneBadge tone="danger">疑似广告</ToneBadge> : null}
              {!p.is_ad && p.genres?.length ? <ToneBadge tone="neutral">{p.genres.slice(0, 3).join(' / ')}</ToneBadge> : null}
            </div>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-3 rounded-lg border border-dashed border-border px-4 py-5">
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-muted text-muted-foreground"><ListFilter className="size-4" /></span>
          <div>
            <p className="text-sm font-medium">没有识别出影视资源</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{result.confidence?.note ?? '按当前规则这条消息会被过滤，不会转存。'}</p>
          </div>
        </div>
      )}

      {p?.is_media && result.confidence ? <ConfidenceBlock confidence={result.confidence} /> : null}

      {p?.reason ? (
        <Section title="判断理由">
          <p className="text-[13px] leading-6 text-foreground/85">{p.reason}</p>
        </Section>
      ) : null}

      {result.trail?.length ? <Trail entries={result.trail} /> : null}
    </div>
  );
}

export default function SourcesPage() {
  const [data, setData] = useState<ISourcesResp | null>(null);
  const [source, setSource] = useState('');
  const [text, setText] = useState(SAMPLE);
  const [urls, setUrls] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ITestParseResult | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    extras.sources().then((d) => {
      setData(d);
      setSource(String(d.sources[0]?.bot ?? ''));
    }).catch(() => setData({ sources: [], save_dirs: {} }));
  }, []);

  const run = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const r = (await extras.testParse(text, urls.split(/[\s,，]+/).filter(Boolean), source || undefined)) as ITestParseResult;
      setResult(r);
      // 窄屏下结果在表单下方，识别完自动滚过去。
      if (window.matchMedia('(max-width: 1023px)').matches) {
        requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '识别失败');
    } finally {
      setBusy(false);
    }
  };

  const sources = data?.sources ?? [];
  const current = sources.find((s) => String(s.bot) === source);
  const sourceHint = current
    ? [
        current.approval_mode ? `审批：${approvalShortLabels[String(current.approval_mode)] ?? '默认'}` : '',
        current.save_to ? `保存到：${String(current.save_to)}` : '',
      ].filter(Boolean).join(' · ')
    : '';
  const decisionMeta = result ? DECISION_META[result.decision] : null;

  return (
    <Page
      title="识别测试"
      description="用当前设置对一条消息做一次完整识别：类型、片名、置信度和转存建议。只做分析，不会转存，也不会产生任务。"
    >
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Panel title="消息内容" icon={MessageSquareText} description="原样粘贴资源 Bot 发来的消息">
          <form
            className="space-y-4"
            onSubmit={(e) => { e.preventDefault(); void run(); }}
          >
            <Field label="按哪个来源的规则识别" htmlFor="test-source" hint={sourceHint || '不同来源可以有各自的过滤、决策与审批规则。'}>
              <Select
                id="test-source"
                value={source}
                onValueChange={setSource}
                disabled={!data}
                placeholder={!data ? '正在读取来源…' : sources.length ? undefined : '默认规则'}
                options={sources.map((s) => ({ value: String(s.bot), label: String(s.bot) }))}
              />
            </Field>
            <Field
              label="消息正文"
              htmlFor="test-text"
              extra={text !== SAMPLE ? (
                <button type="button" onClick={() => setText(SAMPLE)} className="text-xs font-medium text-primary hover:underline">
                  填入示例
                </button>
              ) : null}
            >
              <Textarea
                id="test-text"
                className="min-h-48 bg-card text-[13px] leading-6"
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void run(); } }}
                placeholder="把资源 Bot 发来的消息原样粘贴到这里"
              />
            </Field>
            <Field label="跳转链接（可选）" htmlFor="test-urls" hint="消息按钮背后的真实链接；多个用空格或逗号分隔。">
              <Input
                id="test-urls"
                className="bg-card"
                placeholder="https://115cdn.com/s/…"
                value={urls}
                onChange={(e) => setUrls(e.target.value)}
              />
            </Field>
            <div className="flex items-center gap-3 pt-1">
              <Button type="submit" className="h-10 flex-1 sm:flex-none sm:px-6" disabled={busy || !text.trim()}>
                {busy ? <LoaderCircle className="animate-spin" /> : <Play />}
                {busy ? '正在识别…' : '开始识别'}
              </Button>
              <span className="hidden text-xs text-muted-foreground sm:inline">
                也可以按 <kbd className="rounded border border-border bg-muted px-1 font-sans text-[11px]">⌘/Ctrl</kbd> + <kbd className="rounded border border-border bg-muted px-1 font-sans text-[11px]">Enter</kbd>
              </span>
            </div>
          </form>
        </Panel>

        <div ref={resultRef} className="scroll-mt-20">
          <Panel
            title="识别结果"
            icon={ScanSearch}
            description={decisionMeta?.hint ?? '与真实流程一致，但只用于预览，不会保存'}
            actions={result && decisionMeta ? (
              <ToneBadge tone={decisionMeta.tone} className="px-2.5 py-1 text-[13px]">{decisionLabels[result.decision]}</ToneBadge>
            ) : null}
          >
            {result ? (
              <div className={cn('transition-opacity', busy && 'opacity-50')}>
                <ResultView result={result} />
              </div>
            ) : busy ? (
              <div className="flex min-h-72 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
                <LoaderCircle className="size-5 animate-spin text-primary" />
                正在按当前设置识别…
              </div>
            ) : (
              <EmptyState
                icon={ScanSearch}
                title="粘贴一条消息，点「开始识别」"
                description="会显示识别出的片名、季集与清晰度，置信度的计算依据，以及系统会怎么处理这条消息。"
                className="border-none py-16"
              />
            )}
          </Panel>
        </div>
      </div>
    </Page>
  );
}
