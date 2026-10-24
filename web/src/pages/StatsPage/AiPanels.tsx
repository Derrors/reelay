import { BrainCircuit, Loader2, Radio, Sparkles } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { aiApi, type ILlmUsagePurpose } from '@/api/ai';
import { configApi } from '@/api/config';
import { sourceApi, type IQualityAdvice, type ISourceQuality } from '@/api/sources';
import { ChangeList } from '@/components/app/ChangeList';
import { EmptyState } from '@/components/app/EmptyState';
import { ErrorState } from '@/components/app/ErrorState';
import { Panel } from '@/components/app/Panel';
import { ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useAsync } from '@/hooks/useAsync';
import { percent } from '@/lib/format';
import { AI_PURPOSE_LABELS } from '@/lib/status';
import { cn, errorMessage } from '@/lib/utils';

interface Column<T> {
  label: string;
  value: (row: T) => ReactNode;
  title?: (row: T) => string;
}

/** 指标表：md 以上是表格，窄屏改为每行一张卡片（指标两列排开），不横向滚动。 */
function MetricTable<T>({ rows, rowKey, head, columns, emphasize }: {
  rows: T[]; rowKey: (row: T) => string; head: { label: string; value: (row: T) => ReactNode };
  columns: Array<Column<T>>; emphasize?: (row: T) => boolean;
}) {
  return (
    <>
      <table className="hidden w-full text-xs tabular-nums md:table">
        <thead className="text-left text-muted-foreground">
          <tr className="border-b border-border/70">
            {[head, ...columns].map((c) => <th key={c.label} className="py-2 pr-3 font-medium">{c.label}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {rows.map((row) => (
            <tr key={rowKey(row)} className={cn(emphasize?.(row) && 'font-medium')}>
              <td className="py-2 pr-3">{head.value(row)}</td>
              {columns.map((c) => <td key={c.label} className="py-2 pr-3" title={c.title?.(row)}>{c.value(row)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="space-y-2.5 md:hidden">
        {rows.map((row) => (
          <li key={rowKey(row)} className={cn('rounded-lg border border-border bg-background/60 p-3 text-xs', emphasize?.(row) && 'bg-muted/50')}>
            <div className="text-[13px] font-medium">{head.value(row)}</div>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 tabular-nums">
              {columns.map((c) => (
                <div key={c.label} className="flex min-w-0 justify-between gap-2" title={c.title?.(row)}>
                  <dt className="truncate text-muted-foreground">{c.label}</dt>
                  <dd className="shrink-0">{c.value(row)}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}

const QUALITY_COLUMNS: Array<Column<ISourceQuality>> = [
  { label: '新收', value: (r) => r.received },
  { label: '广告', value: (r) => percent(r.ad_rate) },
  { label: '过滤', value: (r) => percent(r.filter_rate) },
  { label: '重复', value: (r) => percent(r.duplicate_rate) },
  { label: '分享失效', value: (r) => percent(r.unavailable_rate), title: (r) => `${r.unavailable} 条分享在转存前已过期或取消` },
  { label: '批准率', value: (r) => percent(r.approve_rate), title: (r) => `人工确认 ${r.decisions} 次` },
  { label: '修正率', value: (r) => percent(r.correct_rate) },
  { label: 'TMDB 匹配', value: (r) => percent(r.tmdb_match_rate) },
  { label: '入库清晰度', value: (r) => r.resolution ?? '—' },
];

/** 按来源的质量统计（规则计算），可请 AI 评估并给出规则调整，确认后保存。 */
export function SourceQualityPanel({ days }: { days: number }) {
  const quality = useAsync(() => sourceApi.quality(days), [days], '读取来源质量失败');
  const rows = quality.stale ? undefined : quality.data?.sources;
  // 评估结果连同时间范围一起存：换了时间范围，旧的评估作废
  const [advice, setAdvice] = useState<{ days: number; result: IQualityAdvice } | null>(null);
  const shown = advice?.days === days ? advice.result : null;
  const [busy, setBusy] = useState(false);

  const evaluate = async () => {
    setBusy(true);
    try {
      setAdvice({ days, result: await sourceApi.qualityAdvice(days) });
    } catch (e) {
      toast.error(errorMessage(e, 'AI 评估失败'));
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!shown) return;
    setBusy(true);
    try {
      await configApi.saveSections(shown.sections, shown.revision);
      toast.success('屏蔽关键词已保存，新消息按新规则过滤');
      setAdvice({ days, result: { ...shown, changes: [], sections: {} } });
    } catch (e) {
      toast.error(errorMessage(e, '保存失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="来源质量" icon={Radio} description={`近 ${days} 天各来源的消息构成与人工确认情况`}
      actions={rows?.length ? (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void evaluate()}>
          {busy && !shown ? <Loader2 className="animate-spin" /> : <Sparkles />}AI 评估
        </Button>
      ) : null}>
      {!rows ? (
        quality.error
          ? <ErrorState title="读取来源质量失败" error={quality.error} onRetry={quality.reload} retrying={quality.loading} className="border-none py-6" />
          : <Skeleton className="h-24" />
      ) : !rows.length ? (
        <EmptyState icon={Radio} title="这段时间没有收到消息" className="border-none py-6" />
      ) : (
        <div className="space-y-4">
          <MetricTable
            rows={rows}
            rowKey={(r) => r.source}
            head={{
              label: '来源',
              value: (r) => (
                <>
                  <div className="font-medium">{r.source}</div>
                  {r.flags.length ? (
                    <div className="mt-1 flex flex-wrap gap-1">{r.flags.map((f) => <ToneBadge key={f} tone="warning">{f}</ToneBadge>)}</div>
                  ) : null}
                </>
              ),
            }}
            columns={QUALITY_COLUMNS}
          />
          {shown ? (
            <div className="space-y-3 rounded-lg border border-primary/25 bg-primary/[0.04] p-3.5">
              {shown.items.map((item) => (
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
              {shown.changes.length ? (
                <>
                  <ChangeList changes={shown.changes} />
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

type UsageRow = Omit<ILlmUsagePurpose, 'avg_ms'> & { avg_ms: number | null; total?: boolean };

const USAGE_COLUMNS: Array<Column<UsageRow>> = [
  { label: '调用', value: (p) => p.calls },
  { label: '成功', value: (p) => p.ok },
  { label: '未通过校验', value: (p) => p.invalid || '—' },
  { label: '拒答 / 出错', value: (p) => p.refused + p.error || '—' },
  { label: '平均耗时', value: (p) => (p.avg_ms ? `${(p.avg_ms / 1000).toFixed(1)} 秒` : '—') },
  { label: 'token（输入 / 输出）', value: (p) => `${p.prompt_tokens.toLocaleString()} / ${p.completion_tokens.toLocaleString()}` },
];

/** AI 调用记录：按用途的次数、成功与未通过校验、耗时与 token。 */
export function AiUsagePanel({ days }: { days: number }) {
  const loaded = useAsync(() => aiApi.usage(Math.min(days, 90)), [days], '读取 AI 调用记录失败');
  const usage = loaded.stale ? undefined : loaded.data;
  const rows: UsageRow[] = usage ? [...usage.purposes, { ...usage.total, purpose: '合计', avg_ms: null, total: true }] : [];

  return (
    <Panel title="AI 调用" icon={BrainCircuit}
      description={usage?.models.length ? `模型：${usage.models.join('、')}` : '每次调用 AI 的用途、结果与用量（保留 90 天）'}>
      {!usage ? (
        loaded.error
          ? <ErrorState title="读取 AI 调用记录失败" error={loaded.error} onRetry={loaded.reload} retrying={loaded.loading} className="border-none py-6" />
          : <Skeleton className="h-24" />
      ) : !usage.total.calls ? (
        <EmptyState icon={BrainCircuit} title="这段时间没有调用 AI" className="border-none py-6" />
      ) : (
        <MetricTable
          rows={rows}
          rowKey={(p) => p.purpose}
          head={{ label: '用途', value: (p) => (p.total ? '合计' : AI_PURPOSE_LABELS[p.purpose] ?? p.purpose) }}
          columns={USAGE_COLUMNS}
          emphasize={(p) => Boolean(p.total)}
        />
      )}
    </Panel>
  );
}
