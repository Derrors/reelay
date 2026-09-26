import { useEffect, useMemo, useState } from 'react';
import { Check, Eye, RefreshCw, RotateCcw, Search, X } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import PageHeader from '@/components/PageHeader';
import StatusBadge from '@/components/StatusBadge';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { formatDateTime, STATE_META } from '@/lib/status';
import type { IParsedResource, ITaskDetail, ITaskSummary, TaskState } from '@/lib/types';

const FILTER_STATES: Array<{ value: TaskState | ''; label: string }> = [
  { value: '', label: '全部状态' },
  ...Object.entries(STATE_META).map(([value, meta]) => ({ value: value as TaskState, label: meta.label })),
];

function parseEpisodes(value: string): number[] {
  return value.split(/[，,\s]+/).map(Number).filter((item) => Number.isInteger(item) && item > 0);
}

export default function TasksPage() {
  const [items, setItems] = useState<ITaskSummary[]>([]);
  const [state, setState] = useState<TaskState | ''>('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ITaskDetail | null>(null);
  const [draft, setDraft] = useState<Partial<IParsedResource>>({});
  const [episodesText, setEpisodesText] = useState('');
  const [targetDir, setTargetDir] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const result = await api.tasks({ state, query });
      setItems(result.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '读取任务失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    api.tasks({ state }).then((result) => {
      if (!active) return;
      setItems(result.items);
      setLoading(false);
    }).catch((err: unknown) => {
      if (!active) return;
      toast.error(err instanceof Error ? err.message : '读取任务失败');
      setLoading(false);
    });
    return () => { active = false; };
  }, [state]);

  const openTask = async (taskId: string) => {
    try {
      const detail = await api.task(taskId);
      setSelected(detail);
      setDraft(detail.context.parsed ?? {});
      setEpisodesText((detail.context.parsed?.episodes ?? []).join(', '));
      setTargetDir(detail.context.save_dir_alias ?? String(detail.context.source_rule.save_to ?? ''));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '读取任务详情失败');
    }
  };

  const runAction = async (action: 'approve' | 'reject' | 'retry' | 'reparse' | 'reorganize') => {
    if (!selected) return;
    setBusy(true);
    try {
      await api.action(
        selected.task_id,
        action,
        action === 'approve' || action === 'reorganize'
          ? { save_dir_alias: targetDir || null }
          : undefined,
      );
      toast.success({ approve: '已批准任务', reject: '已忽略任务', retry: '已重新排队', reparse: '已回到解析队列', reorganize: '已回到整理队列' }[action]);
      setSelected(null);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };

  const saveParsed = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const values: Partial<IParsedResource> = {
        title: draft.title,
        media_type: draft.media_type,
        year: draft.year ? Number(draft.year) : null,
        season: draft.season ? Number(draft.season) : null,
        episodes: parseEpisodes(episodesText),
        resolution: draft.resolution,
        reason: draft.reason,
      };
      const detail = await api.patchParsed(selected.task_id, values);
      setSelected(detail);
      setDraft(detail.context.parsed ?? {});
      toast.success('识别字段已保存');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const availableActions = useMemo(() => {
    const current = selected?.state;
    return {
      approve: current === 'pending_approval',
      reject: current === 'pending_approval',
      retry: current === 'failed',
      reparse: !!current && !['saving', 'done'].includes(current),
      reorganize: current === 'need_manual',
      editable: !!current && !['saving', 'done'].includes(current),
    };
  }, [selected]);

  return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader
        eyebrow="任务中心"
        title="审批、重试与纠错"
        description="每一行对应一条持久化任务。列表状态、详情状态和 Worker 消费状态使用同一份 SQLite 记录。"
        action={<Button variant="outline" onClick={() => void load()}><RefreshCw className="size-4" />刷新</Button>}
      />

      <div className="mt-6 flex flex-col gap-3 border-b pb-5 sm:flex-row">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void load(); }} placeholder="搜索片名、来源或错误" className="pl-9" />
        </div>
        <NativeSelect value={state} onChange={(event) => { setLoading(true); setState(event.target.value as TaskState | ''); }} className="w-full sm:w-44">
          {FILTER_STATES.map((option) => <NativeSelectOption key={option.value || 'all'} value={option.value}>{option.label}</NativeSelectOption>)}
        </NativeSelect>
        <Button onClick={() => void load()}>查询</Button>
      </div>

      <div className="mt-5 overflow-hidden rounded-md border bg-card">
        {loading ? (
          <div className="space-y-2 p-4">{[1, 2, 3, 4].map((item) => <Skeleton key={item} className="h-12 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="p-12 text-center">
            <div className="text-sm font-medium">当前没有匹配任务</div>
            <div className="mt-2 text-xs text-muted-foreground">监听器收到消息后，任务会按时间出现在这里。</div>
          </div>
        ) : (
          <Table>
            <TableHeader><TableRow>
              <TableHead>状态</TableHead><TableHead>影视资源</TableHead><TableHead>来源</TableHead><TableHead>更新时间</TableHead><TableHead className="text-right">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow key={item.task_id}>
                  <TableCell><StatusBadge state={item.state} /></TableCell>
                  <TableCell className="max-w-[320px]"><div className="truncate font-medium">{item.title || '尚未识别片名'}</div>{item.error ? <div className="mt-1 truncate text-xs text-destructive">{item.error}</div> : null}</TableCell>
                  <TableCell className="text-muted-foreground">{item.source || '—'}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{formatDateTime(item.updated_at)}</TableCell>
                  <TableCell className="text-right"><Button size="sm" variant="ghost" onClick={() => void openTask(item.task_id)}><Eye className="size-4" />查看</Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <Dialog open={!!selected} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto rounded-md">
          {selected ? (
            <>
              <DialogHeader>
                <div className="flex items-center gap-3"><StatusBadge state={selected.state} /><span className="font-mono text-xs text-muted-foreground">{selected.task_id.slice(0, 12)}</span></div>
                <DialogTitle className="pt-2">{selected.context.parsed?.title || '尚未识别片名'}</DialogTitle>
                <DialogDescription>{selected.context.raw.source} · {formatDateTime(selected.updated_at)}</DialogDescription>
              </DialogHeader>

              <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
                <section>
                  <h3 className="mb-2 text-sm font-semibold">原始消息</h3>
                  <div className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md border bg-muted/50 p-3 text-sm leading-6">{selected.context.raw.text}</div>
                </section>
                <section>
                  <h3 className="mb-2 text-sm font-semibold">识别字段</h3>
                  <div className="grid grid-cols-2 gap-3">
                    <label className="text-xs text-muted-foreground">片名<Input disabled={!availableActions.editable} value={String(draft.title ?? '')} onChange={(event) => setDraft({ ...draft, title: event.target.value })} className="mt-1" /></label>
                    <label className="text-xs text-muted-foreground">类型<NativeSelect disabled={!availableActions.editable} value={String(draft.media_type ?? '')} onChange={(event) => setDraft({ ...draft, media_type: event.target.value })} className="mt-1 w-full"><NativeSelectOption value="">未识别</NativeSelectOption><NativeSelectOption value="movie">电影</NativeSelectOption><NativeSelectOption value="tv">剧集</NativeSelectOption></NativeSelect></label>
                    <label className="text-xs text-muted-foreground">年份<Input disabled={!availableActions.editable} type="number" value={String(draft.year ?? '')} onChange={(event) => setDraft({ ...draft, year: Number(event.target.value) || null })} className="mt-1" /></label>
                    <label className="text-xs text-muted-foreground">季<Input disabled={!availableActions.editable} type="number" value={String(draft.season ?? '')} onChange={(event) => setDraft({ ...draft, season: Number(event.target.value) || null })} className="mt-1" /></label>
                    <label className="col-span-2 text-xs text-muted-foreground">集号（逗号分隔）<Input disabled={!availableActions.editable} value={episodesText} onChange={(event) => setEpisodesText(event.target.value)} className="mt-1" /></label>
                    <label className="text-xs text-muted-foreground">分辨率<Input disabled={!availableActions.editable} value={String(draft.resolution ?? '')} onChange={(event) => setDraft({ ...draft, resolution: event.target.value })} className="mt-1" /></label>
                    <label className="text-xs text-muted-foreground">保存目录<Input disabled={!availableActions.editable} value={targetDir} onChange={(event) => setTargetDir(event.target.value)} className="mt-1" /></label>
                    <label className="col-span-2 text-xs text-muted-foreground">识别理由<Textarea disabled={!availableActions.editable} value={String(draft.reason ?? '')} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} className="mt-1 min-h-20" /></label>
                  </div>
                </section>
              </div>

              <section>
                <h3 className="mb-2 text-sm font-semibold">处理记录</h3>
                <div className="border-l border-primary/30 pl-4">
                  {selected.context.trail.length ? selected.context.trail.map((line, index) => <div key={`${line}-${index}`} className="relative py-1.5 text-xs text-muted-foreground before:absolute before:-left-[19px] before:top-3 before:size-2 before:rounded-full before:bg-primary">{line}</div>) : <div className="text-xs text-muted-foreground">暂无处理记录</div>}
                </div>
              </section>

              <DialogFooter className="flex-wrap sm:justify-between">
                <div className="flex flex-wrap gap-2">
                  {availableActions.editable ? <Button variant="outline" disabled={busy} onClick={() => void saveParsed()}>保存识别字段</Button> : null}
                  {availableActions.reparse ? <Button variant="outline" disabled={busy} onClick={() => void runAction('reparse')}><RotateCcw className="size-4" />重新解析</Button> : null}
                  {availableActions.retry ? <Button variant="outline" disabled={busy} onClick={() => void runAction('retry')}><RefreshCw className="size-4" />重试</Button> : null}
                  {availableActions.reorganize ? <Button variant="outline" disabled={busy} onClick={() => void runAction('reorganize')}>重新整理</Button> : null}
                </div>
                <div className="flex gap-2">
                  {availableActions.reject ? <Button variant="destructive" disabled={busy} onClick={() => void runAction('reject')}><X className="size-4" />忽略</Button> : null}
                  {availableActions.approve ? <Button disabled={busy} onClick={() => void runAction('approve')}><Check className="size-4" />批准转存</Button> : null}
                </div>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
