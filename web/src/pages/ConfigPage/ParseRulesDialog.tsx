import { Download, Eraser, Loader2, Play, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { sourceApi, type IParseRulesPreview } from '@/api/sources';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { compactEpisodes } from '@/lib/format';
import { PARSE_RULE_FIELDS, PARSE_RULE_LABELS, type ParseRuleField } from '@/lib/status';
import { cn, errorMessage } from '@/lib/utils';
import { text, type Obj } from '@/lib/values';
import { SampleList } from './SampleList';

const MAX_SAMPLES = 20;
const PLACEHOLDERS: Record<ParseRuleField, string> = {
  title: '如 ^名称：(.+?)\\s*\\(\\d{4}\\)', year: '如 \\((\\d{4})\\)', season: '如 第(\\d+)季',
  episodes: '如 E(\\d+)，区间用 (?P<start>\\d+) 与 (?P<end>\\d+)', resolution: '如 (2160p|1080p)', size: '如 ([\\d.]+\\s*GB)',
};

type Rules = Partial<Record<ParseRuleField, string>>;
type Busy = 'fetch' | 'draft' | 'preview' | null;

/** 只保留写了内容的字段；全空时为 undefined（来源不保存 parse_rules）。 */
function filled(rules: Rules): Rules | undefined {
  const out = Object.fromEntries(Object.entries(rules).filter(([, v]) => v?.trim())) as Rules;
  return Object.keys(out).length ? out : undefined;
}

interface ParseRulesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bot: string;
  parser: string;
  rules: Obj;
  /** 写回来源设置（undefined 表示清除），仍需用户在页面底部保存。 */
  onSave: (rules: Rules | undefined) => void;
}

/** 来源的识别规则：取样例消息（读取频道或手动粘贴）→ AI 起草或手写正则 → 逐条试跑 → 写回来源设置。
 *  打开时才挂载（见 SourcesTab），每次打开都从已保存的规则开始。 */
export function ParseRulesDialog({ open, onOpenChange, bot, parser, rules: saved, onSave }: ParseRulesDialogProps) {
  const [rules, setRules] = useState<Rules>(
    () => Object.fromEntries(PARSE_RULE_FIELDS.map(f => [f, text(saved[f])])) as Rules);
  const [samples, setSamples] = useState<string[]>(['']);
  const [origin, setOrigin] = useState('');
  const [result, setResult] = useState<IParseRulesPreview | null>(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState<Busy>(null);

  const texts = samples.filter(s => s.trim());
  const run = async (kind: Busy, fn: () => Promise<void>) => {
    setBusy(kind);
    try {
      await fn();
    } catch (e) {
      toast.error(errorMessage(e, '操作失败'));
    } finally {
      setBusy(null);
    }
  };

  const fetchMessages = () => run('fetch', async () => {
    const r = await sourceApi.messages(bot.trim(), 10);
    setSamples(r.messages.map(m => m.text));
    setOrigin(r.origin === 'telegram'
      ? `已从 ${bot.trim()} 读取最近 ${r.messages.length} 条消息`
      : `没能实时读取（${r.problem ?? '原因未知'}），用的是这个来源最近收到的 ${r.messages.length} 条消息`);
    setResult(null);
  });
  const draft = () => run('draft', async () => {
    const r = await sourceApi.draftParseRules(bot.trim(), texts);
    setRules(Object.fromEntries(PARSE_RULE_FIELDS.map(f => [f, r.rules[f] ?? ''])) as Rules);
    setResult(r);
    setStale(false);
  });
  const preview = () => run('preview', async () => {
    setResult(await sourceApi.previewParseRules(filled(rules) ?? {}, texts));
    setStale(false);
  });

  const usage = parser === 'regex'
    ? '这个来源用规则识别，消息按这些规则提取。'
    : 'AI 识别时把规则提取的结果作为参考一起交给 AI；AI 不可用时按规则识别兜底。';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] max-w-3xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="space-y-1 px-5 pb-3 pr-12 pt-5 text-left">
          <DialogTitle className="text-base">识别规则 · {bot.trim() || '未命名来源'}</DialogTitle>
          <DialogDescription className="text-xs leading-5">
            按字段写正则，从这个来源的消息里取片名、季集等；没写或在某条消息上没匹配到的字段，沿用通用规则。{usage}
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 pb-4">
          <section className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[13px] font-medium">样例消息</span>
              <Button size="sm" variant="outline" disabled={!!busy || !bot.trim()} onClick={() => void fetchMessages()}
                title={bot.trim() ? '用监听账号读取这个来源最近的 10 条消息' : '先填写来源'}>
                {busy === 'fetch' ? <Loader2 className="animate-spin" /> : <Download />}获取最近消息
              </Button>
            </div>
            {origin ? <p className="text-xs text-muted-foreground">{origin}</p> : null}
            <SampleList samples={samples} onChange={setSamples} max={MAX_SAMPLES} mono />
          </section>

          <section className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[13px] font-medium">规则</span>
              <Button size="sm" variant="outline" disabled={!!busy || !texts.length} onClick={() => void draft()}
                title="AI 对照样例与通用规则的结果，为取错或取不到的字段写正则">
                {busy === 'draft' ? <Loader2 className="animate-spin" /> : <Sparkles />}AI 生成规则
              </Button>
            </div>
            <div className="grid gap-2">
              {PARSE_RULE_FIELDS.map(field => (
                <label key={field} className="grid grid-cols-[4rem_minmax(0,1fr)] items-center gap-3">
                  <span className="text-xs text-muted-foreground">{PARSE_RULE_LABELS[field]}</span>
                  <Input value={rules[field] ?? ''} className="h-9 bg-card font-mono text-xs sm:h-8" placeholder={`沿用通用规则；${PLACEHOLDERS[field]}`}
                    onChange={(e) => { setRules(prev => ({ ...prev, [field]: e.target.value })); setStale(true); }} />
                </label>
              ))}
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              Python 正则，按行匹配（^ 与 $ 是每行的首尾），取第一个捕获组。「集」会逐处匹配后合并；只写 (?P&lt;end&gt;…) 时表示第 1 集到第 end 集；几种写法可以用 | 连起来。
            </p>
            {result?.notes?.length ? (
              <ul className="list-disc space-y-0.5 pl-4 text-xs leading-5 text-muted-foreground">{result.notes.map(n => <li key={n}>{n}</li>)}</ul>
            ) : null}
          </section>

          {result ? (
            <section className="space-y-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-[13px] font-medium">试跑结果</span>
                <span className="text-xs text-muted-foreground">
                  <span className="font-medium text-primary">蓝色</span>来自这个来源的规则，其余来自通用规则
                </span>
              </div>
              {stale ? <p className="text-xs text-warning">规则改过了，点「试跑」看新的结果。</p> : null}
              <ul className={cn('space-y-1.5', stale && 'opacity-60')}>
                {result.results.map((r, i) => {
                  const mark = (field: ParseRuleField) => (r.from_rules.includes(field) ? 'font-medium text-primary' : '');
                  const items: Array<[ParseRuleField, string | null]> = [
                    ['title', r.title], ['year', r.year ? String(r.year) : null],
                    ['season', r.season !== null ? `S${String(r.season).padStart(2, '0')}` : null],
                    ['episodes', r.episodes.length ? compactEpisodes(r.episodes, 4) : null],
                    ['resolution', r.resolution], ['size', r.size],
                  ];
                  return (
                    <li key={i} className="rounded-md bg-card px-2.5 py-1.5 text-xs leading-5">
                      <span className="block truncate text-muted-foreground">{r.preview || '（空消息）'}</span>
                      <span className="flex flex-wrap gap-x-3">
                        {items.map(([field, value]) => (
                          <span key={field}>
                            <span className="text-muted-foreground">{PARSE_RULE_LABELS[field]} </span>
                            <span className={value ? mark(field) : 'text-muted-foreground'}>{value ?? '—'}</span>
                          </span>
                        ))}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}
        </div>
        <DialogFooter className="gap-2 border-t px-5 py-3 sm:justify-between">
          {Object.keys(saved).length ? (
            <Button variant="ghost" className="text-muted-foreground" disabled={!!busy}
              onClick={() => { onSave(undefined); onOpenChange(false); }}>
              <Eraser />清除规则
            </Button>
          ) : <span />}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={!!busy || !texts.length} onClick={() => void preview()}>
              {busy === 'preview' ? <Loader2 className="animate-spin" /> : <Play />}试跑
            </Button>
            <Button disabled={!!busy} onClick={() => { onSave(filled(rules)); onOpenChange(false); }}>保存规则</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
