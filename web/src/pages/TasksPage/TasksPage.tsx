import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, Eye, RefreshCw, RotateCcw, Search, X, MessageSquareText, Sparkles, History } from 'lucide-react';
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
import { Realtime } from '@/lib/realtime';
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

  // 当前筛选条件的 ref，供 SSE 静默刷新时读取最新值。
  const filtersRef = useRef({ state: '' as TaskState | '', query: '' });
  useEffect(() => {
    filtersRef.current = { state, query };
  }, [state, query]);

  // silent=true 时不展示整表骨架，避免实时推送导致列表频繁闪烁。
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const result = await api.tasks(filtersRef.current);
      setItems(result.items);
    } catch (err) {
      if (!silent) toast.error(err instanceof Error ? err.message : '读取任务失败');
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

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

  useEffect(() => {
    // 任务状态推进时静默刷新列表；打开详情弹窗时不打断用户查看/编辑。
    const realtime = new Realtime({
      scopes: ['tasks', 'counts'],
      onChange: () => { if (!selected) void load(true); },
    });
    realtime.start();
    return () => realtime.stop();
  }, [load, selected]);

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

  const runAction = async (action: 'approve' | 'reject' | 'retry' | 'reparse' | 'reorganize' | 'reconcile') => {
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
      toast.success({ approve: '已确认，开始转存', reject: '已忽略这条资源', retry: '已重新排队', reparse: '已回到识别队列', reorganize: '已回到整理队列', reconcile: '已排队核验转存' }[action]);
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
        season: draft.season == null ? null : Number(draft.season),
        episodes: parseEpisodes(episodesText),
        resolution: draft.resolution,
        reason: draft.reason,
      };
      const detail = await api.patchParsed(selected.task_id, values);
      setSelected(detail);
      setDraft(detail.context.parsed ?? {});
      toast.success('识别信息已保存');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const availableActions = useMemo(() => {
    const current = selected?.state;
    const storage = selected?.context.meta.storage;
    const hasLanded = Array.isArray(storage?.landed) && storage.landed.length > 0;
    const idle = !!current && !['received', 'parsed', 'queued', 'saving', 'stored', 'organizing', 'done'].includes(current);
    const transferUnknown = storage?.phase === 'legacy_uncertain' || (!!storage?.manifest && storage.phase !== 'stored');
    return {
      approve: current === 'pending_approval',
      reject: current === 'pending_approval',
      retry: current === 'failed',
      reparse: idle && !transferUnknown && !storage?.manifest && !hasLanded,
      reorganize: current === 'need_manual' && hasLanded,
      reconcile: current === 'need_manual' && storage?.phase === 'receiving' && !!storage.manifest,
      editable: idle && !transferUnknown,
    };
  }, [selected]);

  return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader
        eyebrow="待办与任务"
        title="每条资源的处理记录"
        description="需要你确认的资源会出现在这里；也可以按状态检索历史任务、修正识别结果或重试失败项。"
        action={<Button variant="outline" onClick={() => void load()}><RefreshCw className="size-4" />刷新</Button>}
      />

      <div className="mt-6 flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void load(); }} placeholder="搜索片名、来源或错误信息" className="h-10 rounded-lg pl-9" />
        </div>
        <NativeSelect value={state} onChange={(event) => { setLoading(true); setState(event.target.value as TaskState | ''); }} className="h-10 w-full rounded-lg sm:w-44">
          {FILTER_STATES.map((option) => <NativeSelectOption key={option.value || 'all'} value={option.value}>{option.label}</NativeSelectOption>)}
        </NativeSelect>
        <Button className="h-10 rounded-lg" onClick={() => void load()}>查询</Button>
      </div>

      <div className="mt-5 overflow-hidden rounded-xl border border-border/70 bg-card shadow-sm">
        {loading ? (
          <div className="space-y-2 p-4">{[1, 2, 3, 4].map((item) => <Skeleton key={item} className="h-12 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="p-14 text-center">
            <div className="mx-auto grid size-12 place-items-center rounded-full bg-muted text-muted-foreground"><InboxIcon /></div>
            <div className="mt-3 text-sm font-medium">暂无匹配的任务</div>
            <div className="mt-1.5 text-xs text-muted-foreground">监听到新消息后，任务会自动出现在这里。</div>
          </div>
        ) : (
          <Table>
            <TableHeader><TableRow className="bg-muted/40 hover:bg-muted/40">
              <TableHead className="h-10">状态</TableHead><TableHead>影视资源</TableHead><TableHead>来源</TableHead><TableHead>更新时间</TableHead><TableHead className="text-right">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow key={item.task_id} className="border-border/60">
                  <TableCell><StatusBadge state={item.state} /></TableCell>
                  <TableCell className="max-w-[340px]"><div className="truncate font-medium">{item.title || '尚未识别片名'}</div>{item.error ? <div className="mt-1 truncate text-xs text-destructive">{item.error}</div> : null}</TableCell>
                  <TableCell className="text-muted-foreground">{item.source || '—'}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDateTime(item.updated_at)}</TableCell>
                  <TableCell className="text-right"><Button size="sm" variant="ghost" onClick={() => void openTask(item.task_id)}><Eye className="size-4" />查看</Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <Dialog open={!!selected} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto rounded-xl">
          {selected ? (
            <>
              <DialogHeader>
                <div className="flex items-center gap-2.5"><StatusBadge state={selected.state} /></div>
                <DialogTitle className="pt-1.5 text-xl">{selected.context.parsed?.title || '尚未识别片名'}</DialogTitle>
                <DialogDescription>来自 {selected.context.raw.source} · {formatDateTime(selected.updated_at)}</DialogDescription>
              </DialogHeader>

              <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
                <section>
                  <h3 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-muted-foreground"><MessageSquareText className="size-3.5" />监听到的原始消息</h3>
                  <div className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg border bg-muted/40 p-3 text-[13px] leading-6">{selected.context.raw.text}</div>
                </section>
                <section>
                  <h3 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-muted-foreground"><Sparkles className="size-3.5" />AI 识别结果（可修改）</h3>
                  <div className="grid grid-cols-2 gap-3">
                    <label className="text-xs text-muted-foreground">片名<Input disabled={!availableActions.editable} value={String(draft.title ?? '')} onChange={(event) => setDraft({ ...draft, title: event.target.value })} className="mt-1" /></label>
                    <label className="text-xs text-muted-foreground">类型<NativeSelect disabled={!availableActions.editable} value={String(draft.media_type ?? '')} onChange={(event) => setDraft({ ...draft, media_type: event.target.value })} className="mt-1 w-full"><NativeSelectOption value="">未识别</NativeSelectOption><NativeSelectOption value="movie">电影</NativeSelectOption><NativeSelectOption value="tv">剧集</NativeSelectOption></NativeSelect></label>
                    <label className="text-xs text-muted-foreground">年份<Input disabled={!availableActions.editable} type="number" value={String(draft.year ?? '')} onChange={(event) => setDraft({ ...draft, year: Number(event.target.value) || null })} className="mt-1" /></label>
                    <label className="text-xs text-muted-foreground">季<Input disabled={!availableActions.editable} type="number" value={String(draft.season ?? '')} onChange={(event) => setDraft({ ...draft, season: event.target.value === '' ? null : Number(event.target.value) })} className="mt-1" /></label>
                    <label className="col-span-2 text-xs text-muted-foreground">集号（用逗号分隔）<Input disabled={!availableActions.editable} value={episodesText} onChange={(event) => setEpisodesText(event.target.value)} className="mt-1" /></label>
                    <label className="text-xs text-muted-foreground">清晰度<Input disabled={!availableActions.editable} value={String(draft.resolution ?? '')} onChange={(event) => setDraft({ ...draft, resolution: event.target.value })} className="mt-1" /></label>
                    <label className="text-xs text-muted-foreground">保存到哪个目录<Input disabled={!availableActions.editable} value={targetDir} onChange={(event) => setTargetDir(event.target.value)} className="mt-1" /></label>
                    <label className="col-span-2 text-xs text-muted-foreground">AI 判断理由<Textarea disabled={!availableActions.editable} value={String(draft.reason ?? '')} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} className="mt-1 min-h-20" /></label>
                  </div>
                </section>
              </div>

              <section>
                <h3 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-muted-foreground"><History className="size-3.5" />处理过程</h3>
                <div className="ml-1 border-l-2 border-primary/25 pl-4">
                  {selected.context.trail.length ? selected.context.trail.map((line, index) => <div key={`${line}-${index}`} className="relative py-1.5 text-xs text-muted-foreground before:absolute before:-left-[19px] before:top-[11px] before:size-2 before:rounded-full before:bg-primary/70">{line}</div>) : <div className="text-xs text-muted-foreground">暂无处理记录</div>}
                </div>
              </section>

              <DialogFooter className="flex-wrap gap-2 sm:justify-between">
                <div className="flex flex-wrap gap-2">
                  {availableActions.editable ? <Button variant="outline" disabled={busy} onClick={() => void saveParsed()}>保存识别结果</Button> : null}
                  {availableActions.reparse ? <Button variant="outline" disabled={busy} onClick={() => void runAction('reparse')}><RotateCcw className="size-4" />重新识别</Button> : null}
                  {availableActions.retry ? <Button variant="outline" disabled={busy} onClick={() => void runAction('retry')}><RefreshCw className="size-4" />重试</Button> : null}
                  {availableActions.reorganize ? <Button variant="outline" disabled={busy} onClick={() => void runAction('reorganize')}>重新整理</Button> : null}
                  {availableActions.reconcile ? <Button variant="outline" disabled={busy} onClick={() => void runAction('reconcile')}>核验转存</Button> : null}
                </div>
                <div className="flex gap-2">
                  {availableActions.reject ? <Button variant="destructive" disabled={busy} onClick={() => void runAction('reject')}><X className="size-4" />忽略</Button> : null}
                  {availableActions.approve ? <Button disabled={busy} onClick={() => void runAction('approve')}><Check className="size-4" />确认转存</Button> : null}
                </div>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function InboxIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 20 0 0 0-1.79 1.11z" /></svg>
  );
}
