import { AlertTriangle, Check, FolderTree, RefreshCw, RotateCcw, ShieldCheck, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { MediaCover } from '@/components/app/MediaCover';
import { MediaMeta } from '@/components/app/MediaMeta';
import { Select } from '@/components/app/Select';
import { Sheet } from '@/components/app/Sheet';
import { StatusBadge, ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { fullTime, relativeTime } from '@/lib/format';
import { CONFIDENCE_CAP_LABELS } from '@/lib/status';
import type { IParsedResource, ITaskDetail } from '@/lib/types';

type Action = 'approve' | 'reject' | 'retry' | 'reparse' | 'reorganize' | 'reconcile';

const ACTION_DONE: Record<Action, string> = {
  approve: '已确认，开始转存', reject: '已忽略这条资源', retry: '已重新排队',
  reparse: '已回到识别队列', reorganize: '已回到整理队列', reconcile: '已排队核验转存',
};

let saveDirsCache: Promise<string[]> | null = null;
/** 目录别名只在首次打开详情时读取一次。 */
function useSaveDirs(enabled: boolean) {
  const [dirs, setDirs] = useState<string[]>([]);
  useEffect(() => {
    if (!enabled) return;
    saveDirsCache ??= api.config()
      .then((r) => Object.keys((r.config.save_dirs as Record<string, string>) ?? {}))
      .catch(() => { saveDirsCache = null; return []; });
    let active = true;
    void saveDirsCache.then((list) => { if (active) setDirs(list); });
    return () => { active = false; };
  }, [enabled]);
  return dirs;
}

function parseEpisodes(text: string): number[] {
  return [...new Set(text.split(/[,，\s]+/).map((v) => Number(v)).filter((v) => Number.isInteger(v) && v > 0))];
}

function Field({ label, children, wide }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <label className={wide ? 'col-span-2 block' : 'block'}>
      <span className="mb-1 block text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function Section({ title, icon: Icon, children }: { title: string; icon?: typeof FolderTree; children: React.ReactNode }) {
  return (
    <section className="space-y-2.5">
      <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
        {Icon ? <Icon className="size-3.5 text-muted-foreground" /> : null}{title}
      </h3>
      {children}
    </section>
  );
}

interface TaskDetailSheetProps {
  taskId: string | null;
  onClose: () => void;
  /** 任务状态被修改后回调，用于刷新列表。 */
  onChanged?: () => void;
}

export function TaskDetailSheet({ taskId, onClose, onChanged }: TaskDetailSheetProps) {
  const [detail, setDetail] = useState<ITaskDetail | null>(null);
  const [draft, setDraft] = useState<Partial<IParsedResource>>({});
  const [episodesText, setEpisodesText] = useState('');
  const [targetDir, setTargetDir] = useState('');
  const [busy, setBusy] = useState(false);
  const dirs = useSaveDirs(taskId !== null);

  const apply = (next: ITaskDetail) => {
    setDetail(next);
    setDraft(next.context.parsed ?? {});
    setEpisodesText((next.context.parsed?.episodes ?? []).join(', '));
    setTargetDir(next.context.save_dir_alias ?? String(next.context.source_rule.save_to ?? ''));
  };

  useEffect(() => {
    if (!taskId) return;
    let active = true;
    api.task(taskId).then((d) => { if (active) apply(d); })
      .catch((err: unknown) => toast.error(err instanceof Error ? err.message : '读取任务详情失败'));
    return () => { active = false; setDetail(null); };
  }, [taskId]);

  const can = useMemo(() => {
    const state = detail?.state;
    const storage = detail?.context.meta.storage as Record<string, unknown> | undefined;
    const landed = Array.isArray(storage?.landed) && (storage.landed as unknown[]).length > 0;
    const idle = !!state && !['received', 'parsed', 'queued', 'saving', 'stored', 'organizing', 'done'].includes(state);
    const unknownTransfer = storage?.phase === 'legacy_uncertain' || (!!storage?.manifest && storage.phase !== 'stored');
    return {
      approve: state === 'pending_approval',
      reject: state === 'pending_approval',
      retry: state === 'failed',
      reparse: idle && !unknownTransfer && !storage?.manifest && !landed,
      reorganize: state === 'need_manual' && landed,
      reconcile: state === 'need_manual' && storage?.phase === 'receiving' && !!storage.manifest,
      editable: idle && !unknownTransfer,
      chooseDir: state === 'pending_approval' || (state === 'need_manual' && landed),
    };
  }, [detail]);

  const run = async (action: Action) => {
    if (!detail) return;
    setBusy(true);
    try {
      const body = action === 'approve' || action === 'reorganize' ? { save_dir_alias: targetDir || null } : undefined;
      await api.action(detail.task_id, action, body);
      toast.success(ACTION_DONE[action]);
      onChanged?.();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };

  const saveParsed = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const next = await api.patchParsed(detail.task_id, {
        title: draft.title, media_type: draft.media_type,
        year: draft.year ? Number(draft.year) : null,
        season: draft.season == null ? null : Number(draft.season),
        episodes: parseEpisodes(episodesText), resolution: draft.resolution, reason: draft.reason,
      });
      apply(next);
      toast.success('识别信息已保存');
      onChanged?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const parsed = detail?.context.parsed;
  const meta = detail?.context.meta ?? {};
  const confidence = meta.confidence as { score?: number; caps?: string[] } | undefined;
  const plan = ((meta.organize as { plan?: Array<{ target_dir: string; target_name: string }> } | undefined)?.plan) ?? [];
  const organizeError = (meta.organize as { error?: string } | undefined)?.error;

  const hasActions = can.editable || can.reparse || can.retry || can.reconcile || can.reorganize || can.approve;
  const footer = detail && hasActions ? (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap gap-2">
        {can.editable ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void saveParsed()}>保存修改</Button> : null}
        {can.reparse ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('reparse')}><RotateCcw />重新识别</Button> : null}
        {can.retry ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('retry')}><RefreshCw />重试</Button> : null}
        {can.reconcile ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('reconcile')}><ShieldCheck />核验转存</Button> : null}
        {can.reorganize ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('reorganize')}><FolderTree />重新整理</Button> : null}
      </div>
      <div className="flex gap-2">
        {can.reject ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('reject')} className="text-destructive"><X />忽略</Button> : null}
        {can.approve ? <Button size="sm" disabled={busy} onClick={() => void run('approve')}><Check />确认转存</Button> : null}
      </div>
    </div>
  ) : null;

  return (
    <Sheet
      open={taskId !== null}
      onOpenChange={(open) => { if (!open) onClose(); }}
      title={detail ? (parsed?.title || '尚未识别片名') : '任务详情'}
      description={detail ? `${detail.context.raw.source} · ${relativeTime(detail.created_at)}` : undefined}
      footer={footer}
    >
      {!detail ? (
        <div className="space-y-3"><Skeleton className="h-32 w-full" /><Skeleton className="h-48 w-full" /></div>
      ) : (
        <div className="space-y-6">
          <div className="flex gap-4">
            <MediaCover title={parsed?.title} posterPath={parsed?.raw_fields?.poster_path as string | undefined}
              mediaType={parsed?.media_type} className="w-20 shrink-0" quality="w185" />
            <div className="min-w-0 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge state={detail.state} />
                {parsed?.year ? <span className="text-sm text-muted-foreground">{parsed.year}</span> : null}
              </div>
              <MediaMeta mediaType={parsed?.media_type} season={parsed?.season} episodes={parsed?.episodes}
                resolution={parsed?.resolution} size={parsed?.size} />
              {confidence?.score !== undefined ? (
                <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  置信度 <span className="font-medium tabular-nums text-foreground">{Math.round(confidence.score * 100)}%</span>
                  {(confidence.caps ?? []).map((cap) => <ToneBadge key={cap} tone="warning">{CONFIDENCE_CAP_LABELS[cap] ?? cap}</ToneBadge>)}
                </div>
              ) : null}
              <div className="text-xs text-muted-foreground">
                {detail.context.save_dir_alias ? <>保存到「{detail.context.save_dir_alias}」 · </> : null}
                <span title={fullTime(detail.updated_at)}>更新于 {relativeTime(detail.updated_at)}</span>
              </div>
            </div>
          </div>

          {detail.error || organizeError ? (
            <div className="flex gap-2.5 rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <span className="break-words">{organizeError || detail.error}</span>
            </div>
          ) : null}

          {can.chooseDir && dirs.length ? (
            <Section title="保存到">
              <Select value={targetDir} onValueChange={setTargetDir} options={dirs.map((d) => ({ value: d, label: d }))} />
            </Section>
          ) : null}

          {can.editable ? (
            <Section title="识别结果（可修改）">
              <div className="grid grid-cols-2 gap-3">
                <Field label="片名" wide><Input value={String(draft.title ?? '')} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field>
                <Field label="类型">
                  <Select value={String(draft.media_type ?? '')} onValueChange={(v) => setDraft({ ...draft, media_type: v || null })}
                    placeholder="未识别" options={[{ value: 'movie', label: '电影' }, { value: 'tv', label: '剧集' }, { value: 'anime', label: '动漫' }]} />
                </Field>
                <Field label="年份"><Input inputMode="numeric" value={String(draft.year ?? '')} onChange={(e) => setDraft({ ...draft, year: Number(e.target.value) || null })} /></Field>
                <Field label="季"><Input inputMode="numeric" value={String(draft.season ?? '')} onChange={(e) => setDraft({ ...draft, season: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
                <Field label="清晰度"><Input value={String(draft.resolution ?? '')} onChange={(e) => setDraft({ ...draft, resolution: e.target.value })} /></Field>
                <Field label="集号（逗号分隔）" wide><Input value={episodesText} onChange={(e) => setEpisodesText(e.target.value)} /></Field>
                <Field label="判断理由" wide><Textarea value={String(draft.reason ?? '')} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} className="min-h-16" /></Field>
              </div>
            </Section>
          ) : parsed?.reason ? (
            <Section title="判断理由">
              <p className="text-sm leading-6 text-muted-foreground">{parsed.reason}</p>
            </Section>
          ) : null}

          {plan.length ? (
            <Section title="整理结果" icon={FolderTree}>
              <ul className="space-y-1 rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-5">
                {plan.map((item) => (
                  <li key={`${item.target_dir}/${item.target_name}`} className="break-all">
                    <span className="text-muted-foreground">{item.target_dir}/</span>{item.target_name}
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          <Section title="原始消息">
            <div className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/40 p-3 text-[13px] leading-6">
              {detail.context.raw.text || '（空消息）'}
            </div>
          </Section>

          <Section title="处理过程">
            <ol className="relative ml-1.5 border-l border-border pl-4">
              {detail.context.trail.length ? detail.context.trail.map((line, index) => (
                <li key={`${index}-${line}`} className="relative pb-2 text-xs leading-5 text-muted-foreground last:pb-0">
                  <span className="absolute -left-[21px] top-1.5 size-2 rounded-full bg-primary/60 ring-2 ring-background" />
                  {line}
                </li>
              )) : <li className="text-xs text-muted-foreground">暂无处理记录</li>}
            </ol>
          </Section>
        </div>
      )}
    </Sheet>
  );
}
