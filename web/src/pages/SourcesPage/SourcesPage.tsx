import { useEffect, useState } from 'react';
import { Film, MessageSquarePlus, Play, Sparkles, Tv, HelpCircle, Filter, CheckCircle2, XCircle, Link2 } from 'lucide-react';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import { extras, type ISourcesResp } from '@/api/extras';
import { decisionLabels } from '@/lib/status';
import type { IParsedResource } from '@/lib/types';

const SAMPLE = '📺 示例剧名 (2026) S01E01 ✨4K WEB-DL\n💾 大小：1.2 GB\n🔗 链接： 点击跳转';

function ResultRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex gap-3 border-b border-border/50 py-2 last:border-b-0">
      <span className="w-20 shrink-0 text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-words text-[13px]">{value ?? <span className="text-muted-foreground">—</span>}</span>
    </div>
  );
}

export default function SourcesPage() {
  const [data, setData] = useState<ISourcesResp | null>(null);
  const [source, setSource] = useState('');
  const [text, setText] = useState(SAMPLE);
  const [urls, setUrls] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ parsed: IParsedResource | null; decision: string } | null>(null);

  useEffect(() => {
    extras.sources().then((d) => {
      setData(d);
      setSource(String(d.sources[0]?.bot ?? ''));
    }).catch(() => undefined);
  }, []);

  const run = async () => {
    setBusy(true);
    try {
      const r = await extras.testParse(text, urls.split(/[\s,]+/).filter(Boolean), source || undefined);
      setResult(r);
      toast.success(`识别完成：${decisionLabels[r.decision] ?? r.decision}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '测试失败');
    } finally {
      setBusy(false);
    }
  };

  const p = result?.parsed;
  const decision = result?.decision;
  const decisionTone = decision === 'save'
    ? 'bg-success/10 text-success'
    : decision === 'ask'
      ? 'bg-warning/10 text-warning'
      : 'bg-muted text-muted-foreground';

  return (
    <div className="mx-auto w-full max-w-5xl p-5 sm:p-8">
      <PageHeader
        eyebrow="识别演练"
        title="粘贴一条消息，看 AI 怎么判断"
        description="用当前设置对任意消息做一次识别演练：会判类型、提取片名清晰度并给出转存建议。整个过程只做分析，不会转存、不会产生任何任务。"
      />
      {!data ? <Skeleton className="mt-6 h-96 rounded-xl" /> : (
        <div className="mt-6 grid gap-5 lg:grid-cols-2">
          <Card className="rounded-xl border-border/70 shadow-sm">
            <CardHeader className="p-5 pb-0">
              <CardTitle className="flex items-center gap-2 text-[15px]"><MessageSquarePlus className="size-[18px] text-primary" />输入消息</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 p-5">
              <label className="block text-xs text-muted-foreground">
                按哪个来源的规则识别
                <select value={source} onChange={(e) => setSource(e.target.value)} className="mt-1 h-10 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus:border-ring focus:ring-2 focus:ring-ring/20">
                  {data.sources.map((s) => <option key={String(s.bot)} value={String(s.bot)}>{String(s.bot)}</option>)}
                </select>
              </label>
              <Textarea className="min-h-52 text-[13px] leading-6" value={text} onChange={(e) => setText(e.target.value)} placeholder="把资源 Bot 发来的消息原样粘贴到这里" />
              <input className="h-10 w-full rounded-lg border border-input px-3 text-[13px] outline-none focus:border-ring focus:ring-2 focus:ring-ring/20" placeholder="消息里的真实跳转链接（可选，多个用逗号分隔）" value={urls} onChange={(e) => setUrls(e.target.value)} />
              <Button className="w-full h-10 rounded-lg" onClick={run} disabled={busy || !text.trim()}>{busy ? <><Sparkles className="size-4 animate-pulse" />正在识别…</> : <><Play className="size-4" />开始识别</>}</Button>
            </CardContent>
          </Card>

          <Card className="rounded-xl border-border/70 shadow-sm">
            <CardHeader className="p-5 pb-0">
              <CardTitle className="flex items-center gap-2 text-[15px]"><Sparkles className="size-[18px] text-primary" />识别结果</CardTitle>
            </CardHeader>
            <CardContent className="p-5">
              {!result ? (
                <div className="flex min-h-[320px] flex-col items-center justify-center text-center text-muted-foreground">
                  <div className="grid size-12 place-items-center rounded-full bg-muted"><HelpCircle className="size-6" /></div>
                  <p className="mt-3 text-sm">在左边粘贴消息后点「开始识别」</p>
                  <p className="mt-1 text-xs">结果只用于预览，不会保存或转存</p>
                </div>
              ) : !p ? (
                <div className="flex min-h-[320px] flex-col items-center justify-center text-center">
                  <div className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground"><Filter className="size-6" /></div>
                  <p className="mt-3 text-sm font-medium">这不是影视资源</p>
                  <p className="mt-1 text-xs text-muted-foreground">按当前规则会被自动忽略，不会转存</p>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] font-medium ${decisionTone}`}>
                    {decision === 'save' ? <CheckCircle2 className="size-4" /> : decision === 'ask' ? <HelpCircle className="size-4" /> : <XCircle className="size-4" />}
                    {decisionLabels[decision ?? ''] ?? decision}
                  </div>
                  <div className="rounded-lg bg-muted/40 px-3.5 py-1">
                    <ResultRow label="片名" value={<b>{p.title || '未能识别'}</b>} />
                    <ResultRow label="类型" value={p.media_type === 'movie' ? <span className="inline-flex items-center gap-1"><Film className="size-3.5" />电影</span> : p.media_type === 'tv' ? <span className="inline-flex items-center gap-1"><Tv className="size-3.5" />剧集</span> : '未确定'} />
                    <ResultRow label="年份" value={p.year ?? undefined} />
                    {p.media_type === 'tv' ? <ResultRow label="季 / 集" value={`第 ${p.season ?? 1} 季${p.episodes?.length ? ` · 第 ${p.episodes.join('、')} 集` : ''}`} /> : null}
                    <ResultRow label="清晰度" value={p.resolution} />
                    <ResultRow label="大小" value={p.size} />
                    {(p.share_code || p.receive_code) ? <ResultRow label="115 链接" value={<span className="inline-flex items-center gap-1 text-primary"><Link2 className="size-3.5" />已提取到转存链接</span>} /> : null}
                    {typeof p.confidence === 'number' ? <ResultRow label="把握程度" value={<span className={p.confidence >= 0.6 ? 'text-success' : 'text-warning'}>{Math.round(p.confidence * 100)}%</span>} /> : null}
                  </div>
                  {p.reason ? <div className="rounded-lg bg-primary/[0.05] px-3.5 py-3 text-xs leading-5 text-muted-foreground"><b className="text-foreground/80">判断理由：</b>{p.reason}</div> : null}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
