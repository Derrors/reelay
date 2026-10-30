import { CircleAlert, Loader2, Wand2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { aiApi, type ICompileResult } from '@/api/ai';
import { ChangeList } from '@/components/app/ChangeList';
import { Notice } from '@/components/app/Notice';
import { Panel } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { errorMessage } from '@/lib/utils';
import { rows, type Obj } from '@/lib/values';
import { sourceBots } from './shared';

interface RuleComposerProps {
  config: Obj;
  /** 页面上有未保存的修改时不能直接应用，避免两份改动互相覆盖。 */
  dirty: boolean;
  onApply: (sections: Record<string, unknown>, revision: string) => Promise<boolean>;
}

/** 一句话偏好 → 确定性规则：AI 起草，后端按正式配置校验并列出改动，用户确认后才写入。 */
export function RuleComposer({ config, dirty, onApply }: RuleComposerProps) {
  // 范围按卡片：多来源的卡片共用规则，用第一个来源指代整张卡片
  const sources = rows(config.sources).map(sourceBots).filter((bots) => bots.length);
  const [scope, setScope] = useState('defaults');
  const [request, setRequest] = useState('');
  const [result, setResult] = useState<ICompileResult | null>(null);
  const [busy, setBusy] = useState(false);

  const compile = async () => {
    if (!request.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      setResult(await aiApi.compileRules(request.trim(), scope));
    } catch (e) {
      toast.error(errorMessage(e, '生成规则失败'));
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!result) return;
    setBusy(true);
    try {
      if (await onApply(result.sections, result.revision)) {
        toast.success('规则已保存并立即生效，新消息按新规则判断');
        setResult(null);
        setRequest('');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      icon={Wand2}
      title="用一句话生成规则"
      description="说说想收什么、不要什么，AI 把它写成过滤、分类与版本偏好规则。确认改动后才保存；之后按规则判断，不再为每条消息调用 AI。"
      bodyClassName="p-4 sm:p-5"
    >
      <div className="space-y-3">
        <Textarea value={request} onChange={(e) => setRequest(e.target.value)} className="min-h-20 bg-card"
          placeholder="如：只要 1080p 以上的国产剧和日本动画，综艺不要，优先国语中字版本" aria-label="收藏偏好描述" />
        {/* 作用范围与生成按钮同高、同一行；窄屏上下排列，按钮占满宽度 */}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <label className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 text-[13px] text-muted-foreground">作用于</span>
            <Select className="min-w-0 flex-1 sm:w-64 sm:flex-none" value={scope} onValueChange={setScope}
              options={[{ value: 'defaults', label: '所有来源（默认规则）' }, ...sources.map((bots) => ({ value: bots[0], label: bots.join('、') }))]} />
          </label>
          <Button disabled={busy || !request.trim()} onClick={() => void compile()}>
            {busy && !result ? <Loader2 className="animate-spin" /> : <Wand2 />}生成规则
          </Button>
        </div>

        {result ? (
          <div className="space-y-3 rounded-lg border border-primary/25 bg-primary/[0.04] p-3.5">
            <p className="text-sm font-medium leading-6">{result.summary}</p>
            {result.changes.length ? <ChangeList changes={result.changes} />
              : <p className="text-[13px] text-muted-foreground">现有规则已经满足这句话，不需要修改。</p>}
            {result.unsupported.length ? (
              <Notice tone="warning" icon={CircleAlert} title="这些要求没法用规则表达">
                <ul className="list-disc pl-4">{result.unsupported.map((u) => <li key={u}>{u}</li>)}</ul>
                <p className="mt-1 text-xs">可以写进「收藏偏好」，并把放行方式改为「AI 按偏好判断」。</p>
              </Notice>
            ) : null}
            {dirty && result.changes.length ? (
              <p className="text-xs text-warning">页面上有未保存的修改，请先保存或放弃，再应用这些规则。</p>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setResult(null)}>放弃</Button>
              {result.changes.length ? (
                <Button size="sm" disabled={busy || dirty} onClick={() => void apply()}>
                  {busy ? <Loader2 className="animate-spin" /> : null}应用这些改动
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
