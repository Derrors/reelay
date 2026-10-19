import { Loader2, Plus, Sparkles, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { ai, type ISourceDraft } from '@/api/ai';
import { ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { compactEpisodes } from '@/lib/format';
import { approvalLabels, decisionLabels, type Tone } from '@/lib/status';
import { obj, strings, text, type Obj } from './shared';

const DECISION_TONE: Record<string, Tone> = { save: 'success', ask: 'warning', skip: 'neutral' };
const MAX_SAMPLES = 10;

interface SourceWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 把草案加入页面上的来源列表（同名来源会被替换），仍需用户点保存。 */
  onAdd: (draft: Obj) => void;
}

/** 新来源接入向导：贴几条样例消息，AI 起草来源规则，按草案逐条试跑后由用户决定是否加入。 */
export function SourceWizard({ open, onOpenChange, onAdd }: SourceWizardProps) {
  const [bot, setBot] = useState('');
  const [samples, setSamples] = useState<string[]>(['', '']);
  const [result, setResult] = useState<ISourceDraft | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = () => { setBot(''); setSamples(['', '']); setResult(null); };
  const filled = samples.filter((s) => s.trim());

  const draft = async () => {
    setBusy(true);
    setResult(null);
    try {
      setResult(await ai.draftSource(bot.trim(), filled));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '起草失败');
    } finally {
      setBusy(false);
    }
  };

  const rule = result ? result.draft : {};
  const filters = obj(rule.filters);

  return (
    <Dialog open={open} onOpenChange={(next) => { onOpenChange(next); if (!next) reset(); }}>
      <DialogContent className="flex max-h-[88vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="space-y-1 px-5 pb-3 pr-12 pt-5 text-left">
          <DialogTitle className="text-base">用样例消息接入新来源</DialogTitle>
          <DialogDescription className="text-xs">
            贴几条这个来源的消息（资源、广告、闲聊都可以），AI 起草规则并用它把样例逐条试跑一遍。加入后仍需在页面底部保存。
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 pb-4">
          <label className="block space-y-1.5">
            <span className="text-[13px] font-medium">Bot 或频道</span>
            <Input value={bot} onChange={(e) => setBot(e.target.value)} placeholder="@resource_bot" className="bg-card" />
          </label>
          <div className="space-y-2">
            <span className="text-[13px] font-medium">样例消息</span>
            {samples.map((sample, i) => (
              <div key={i} className="flex gap-2">
                <Textarea value={sample} className="min-h-16 bg-card text-xs" placeholder={`第 ${i + 1} 条消息`}
                  onChange={(e) => setSamples((prev) => prev.map((s, n) => (n === i ? e.target.value : s)))} />
                {samples.length > 1 ? (
                  <Button variant="ghost" size="icon" className="size-8 shrink-0 text-muted-foreground" aria-label="删除这条样例"
                    onClick={() => setSamples((prev) => prev.filter((_, n) => n !== i))}><Trash2 /></Button>
                ) : null}
              </div>
            ))}
            {samples.length < MAX_SAMPLES ? (
              <Button variant="outline" size="sm" onClick={() => setSamples((prev) => [...prev, ''])}><Plus />再加一条</Button>
            ) : null}
          </div>

          {result ? (
            <div className="space-y-3 rounded-lg border border-primary/25 bg-primary/[0.04] p-3.5">
              <p className="text-sm font-medium leading-6">{result.summary}</p>
              <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-[13px] leading-5">
                <dt className="text-muted-foreground">确认方式</dt><dd>{approvalLabels[text(rule.approval_mode)] ?? text(rule.approval_mode)}</dd>
                <dt className="text-muted-foreground">识别方式</dt><dd>{text(rule.parser) === 'regex' ? '规则识别' : 'AI 识别'}</dd>
                <dt className="text-muted-foreground">保存到</dt><dd>{text(rule.save_to)}</dd>
                <dt className="text-muted-foreground">集数依据</dt><dd>{text(rule.episodes_from) === 'message' ? '只转存消息写明的集' : '以分享里的文件为准'}</dd>
                <dt className="text-muted-foreground">必须包含</dt><dd>{strings(filters.must_have).join('、') || '不限'}</dd>
                <dt className="text-muted-foreground">屏蔽关键词</dt><dd>{strings(filters.block_keywords).join('、') || '无'}</dd>
              </dl>
              {result.notes.length ? (
                <ul className="list-disc space-y-0.5 pl-4 text-xs leading-5 text-muted-foreground">{result.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              ) : null}
              <div>
                <p className="mb-1.5 text-xs font-medium text-muted-foreground">按这份规则试跑样例</p>
                <ul className="space-y-1.5">
                  {result.results.map((r, i) => (
                    <li key={i} className="flex items-start gap-2 rounded-md bg-card px-2.5 py-1.5 text-xs leading-5">
                      <ToneBadge tone={DECISION_TONE[r.decision] ?? 'neutral'}>{decisionLabels[r.decision] ?? r.decision}</ToneBadge>
                      <span className="min-w-0">
                        <span className="block truncate">{r.title ? `《${r.title}》` : r.preview || '（空消息）'}
                          {r.episodes?.length ? ` ${compactEpisodes(r.episodes)}` : ''}{r.resolution ? ` · ${r.resolution}` : ''}</span>
                        {r.reason ? <span className="block truncate text-muted-foreground">{r.reason}</span> : null}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              {result.exists ? <p className="text-xs text-warning">已有同名来源，加入后会替换它现在的设置。</p> : null}
            </div>
          ) : null}
        </div>
        <DialogFooter className="border-t px-5 py-3">
          <Button variant="outline" disabled={busy || !bot.trim() || !filled.length} onClick={() => void draft()}>
            {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}{result ? '重新起草' : '起草规则'}
          </Button>
          {result ? (
            <Button disabled={busy} onClick={() => { onAdd(result.draft); onOpenChange(false); reset(); }}>
              <Plus />加入来源列表
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
