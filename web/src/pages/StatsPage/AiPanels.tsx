import { BrainCircuit, Loader2, Radio, Sparkles } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { ai, percent, type ILlmUsage, type IQualityAdvice, type ISourceQuality } from '@/api/ai';
import { api } from '@/api/client';
import { ChangeList } from '@/components/app/ChangeList';
import { EmptyState } from '@/components/app/EmptyState';
import { Panel } from '@/components/app/Panel';
import { ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';

const PURPOSES: Record<string, string> = {
  parse: '识别消息', preference: '按偏好判断', assist: '转人工前兜底', compile: '一句话生成规则', advice: '审批建议措辞',
  subscribe: '订阅解析', source_draft: '来源接入向导', source_quality: '来源质量评估', assistant: '控制 Bot 对话',
  explain: '失败解释', daily: '日报', other: '其他',
};

const message = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

/** 按来源的质量统计（规则计算），可请 AI 评估并给出规则调整，确认后保存。 */
export function SourceQualityPanel({ days }: { days: number }) {
  const [rows, setRows] = useState<ISourceQuality[] | null>(null);
  const [advice, setAdvice] = useState<IQualityAdvice | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    ai.sourceQuality(days)
      .then((r) => { if (active) { setRows(r.sources); setAdvice(null); } })  // 换了时间范围，旧的评估作废
      .catch(() => { if (active) setRows([]); });
    return () => { active = false; };
  }, [days]);

  const evaluate = async () => {
    setBusy(true);
    try {
      setAdvice(await ai.sourceQualityAdvice(days));
    } catch (e) {
      toast.error(message(e, 'AI 评估失败'));
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!advice) return;
    setBusy(true);
    try {
      await api.saveSections(advice.sections, advice.revision);
      toast.success('屏蔽关键词已保存，新消息按新规则过滤');
      setAdvice({ ...advice, changes: [], sections: {} });
    } catch (e) {
      toast.error(message(e, '保存失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="来源质量" icon={Radio} description={`近 ${days} 天各来源的消息构成与人工确认情况`}
      actions={rows?.length ? (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void evaluate()}>
          {busy && !advice ? <Loader2 className="animate-spin" /> : <Sparkles />}AI 评估
        </Button>
      ) : null}>
      {!rows ? <Skeleton className="h-24" /> : !rows.length ? (
        <EmptyState icon={Radio} title="这段时间没有收到消息" className="border-none py-6" />
      ) : (
        <div className="space-y-4">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-xs tabular-nums">
              <thead className="text-left text-muted-foreground">
                <tr className="border-b border-border/70">
                  {['来源', '新收', '广告', '过滤', '重复', '分享失效', '批准率', '修正率', 'TMDB 匹配', '入库清晰度'].map((h) => (
                    <th key={h} className="py-2 pr-3 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {rows.map((r) => (
                  <tr key={r.source}>
                    <td className="py-2 pr-3">
                      <div className="font-medium">{r.source}</div>
                      {r.flags.length ? (
                        <div className="mt-1 flex flex-wrap gap-1">{r.flags.map((f) => <ToneBadge key={f} tone="warning">{f}</ToneBadge>)}</div>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3">{r.received}</td>
                    <td className="py-2 pr-3">{percent(r.ad_rate)}</td>
                    <td className="py-2 pr-3">{percent(r.filter_rate)}</td>
                    <td className="py-2 pr-3">{percent(r.duplicate_rate)}</td>
                    <td className="py-2 pr-3" title={`${r.unavailable} 条分享在转存前已过期或取消`}>{percent(r.unavailable_rate)}</td>
                    <td className="py-2 pr-3" title={`人工确认 ${r.decisions} 次`}>{percent(r.approve_rate)}</td>
                    <td className="py-2 pr-3">{percent(r.correct_rate)}</td>
                    <td className="py-2 pr-3">{percent(r.tmdb_match_rate)}</td>
                    <td className="py-2 pr-3">{r.resolution ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {advice ? (
            <div className="space-y-3 rounded-lg border border-primary/25 bg-primary/[0.04] p-3.5">
              {advice.items.map((item) => (
                <div key={item.source}>
                  <p className="text-[13px] font-medium">{item.source}：{item.summary}</p>
                  {item.suggestions.length ? (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs leading-5 text-muted-foreground">
                      {item.suggestions.map((s) => <li key={s}>{s}</li>)}
                    </ul>
                  ) : null}
                  {item.dropped_keywords.length ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      没有采纳「{item.dropped_keywords.join('」「')}」：你批准过的消息里出现过，屏蔽会误伤。
                    </p>
                  ) : null}
                </div>
              ))}
              {advice.changes.length ? (
                <>
                  <ChangeList changes={advice.changes} />
                  <div className="flex justify-end">
                    <Button size="sm" disabled={busy} onClick={() => void apply()}>应用这些改动</Button>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </Panel>
  );
}

/** AI 调用记录：按用途的次数、成功与未通过校验、耗时与 token。 */
export function AiUsagePanel({ days }: { days: number }) {
  const [usage, setUsage] = useState<ILlmUsage | null>(null);
  useEffect(() => {
    let active = true;
    ai.usage(Math.min(days, 90)).then((r) => { if (active) setUsage(r); }).catch(() => undefined);
    return () => { active = false; };
  }, [days]);

  return (
    <Panel title="AI 调用" icon={BrainCircuit}
      description={usage?.models.length ? `模型：${usage.models.join('、')}` : '每次调用 AI 的用途、结果与用量（保留 90 天）'}>
      {!usage ? <Skeleton className="h-24" /> : !usage.total.calls ? (
        <EmptyState icon={BrainCircuit} title="这段时间没有调用 AI" className="border-none py-6" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-xs tabular-nums">
            <thead className="text-left text-muted-foreground">
              <tr className="border-b border-border/70">
                {['用途', '调用', '成功', '未通过校验', '拒答 / 出错', '平均耗时', 'token（输入 / 输出）'].map((h) => (
                  <th key={h} className="py-2 pr-3 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {[...usage.purposes, { ...usage.total, purpose: '合计', avg_ms: null }].map((p) => (
                <tr key={p.purpose} className={p.purpose === '合计' ? 'font-medium' : undefined}>
                  <td className="py-2 pr-3">{PURPOSES[p.purpose] ?? p.purpose}</td>
                  <td className="py-2 pr-3">{p.calls}</td>
                  <td className="py-2 pr-3">{p.ok}</td>
                  <td className="py-2 pr-3">{p.invalid || '—'}</td>
                  <td className="py-2 pr-3">{p.refused + p.error || '—'}</td>
                  <td className="py-2 pr-3">{p.avg_ms ? `${(p.avg_ms / 1000).toFixed(1)} 秒` : '—'}</td>
                  <td className="py-2 pr-3">{p.prompt_tokens.toLocaleString()} / {p.completion_tokens.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
