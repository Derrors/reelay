import { AlertTriangle, BellPlus, Check, EyeOff, FolderTree, RefreshCw, RotateCcw, ShieldCheck, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { inboxApi, type ISaveDirs } from '@/api/inbox';
import { subscriptionApi } from '@/api/subscriptions';
import { taskApi } from '@/api/tasks';
import { ErrorState } from '@/components/app/ErrorState';
import { Field } from '@/components/app/Field';
import { MediaCover } from '@/components/app/MediaCover';
import { MediaMeta } from '@/components/app/MediaMeta';
import { ConflictDialog, OrganizeConflict } from '@/components/OrganizeConflict';
import { Select } from '@/components/app/Select';
import { Sheet } from '@/components/app/Sheet';
import { StatusBadge, ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useAsync } from '@/hooks/useAsync';
import { CONFLICT_DONE, confirmAbandon, type ConflictChoice } from '@/lib/conflict';
import { fullTime, relativeTime } from '@/lib/format';
import { CONFIDENCE_CAP_LABELS, MEDIA_TYPE_LABELS } from '@/lib/status';
import { announceSubscription } from '@/lib/subscriptions';
import { ACTION_DONE, ACTION_ENDPOINT, actionBody, allows, type PostedAction } from '@/lib/taskActions';
import type { IOrganizeConflict, IParsedResource, ITaskDetail, ITransferPartial, TaskActionName } from '@/lib/types';
import { errorMessage } from '@/lib/utils';

const MEDIA_TYPES = Object.entries(MEDIA_TYPE_LABELS).map(([value, label]) => ({ value, label }));

function parseEpisodes(text: string): number[] {
  return [...new Set(text.split(/[,，\s]+/).map((v) => Number(v)).filter((v) => Number.isInteger(v) && v > 0))];
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
  /** 页面已经有的保存目录（收件箱接口顺带返回）；不传时打开需要选目录的任务再读取。 */
  saveDirs?: ISaveDirs;
}

interface TaskMeta {
  confidence?: { score?: number; caps?: string[] };
  organize?: { plan?: Array<{ target_dir: string; target_name: string }>; error?: string; pending_conflict?: IOrganizeConflict };
  storage?: { partial?: ITransferPartial };
}

export function TaskDetailSheet({ taskId, onClose, onChanged, saveDirs }: TaskDetailSheetProps) {
  const loaded = useAsync(() => (taskId ? taskApi.get(taskId) : Promise.resolve(null)), [taskId], '读取任务详情失败');
  const detail = loaded.stale ? null : loaded.data ?? null;
  const [shown, setShown] = useState<ITaskDetail | null>(null);
  const [draft, setDraft] = useState<Partial<IParsedResource>>({});
  const [episodesText, setEpisodesText] = useState('');
  const [targetDir, setTargetDir] = useState('');
  const [busy, setBusy] = useState(false);
  const [choosing, setChoosing] = useState(false);
  // 换了任务或保存了修改：表单回到这份详情（渲染时同步，而不是在 effect 里再设一次）
  if (detail !== shown) {
    setShown(detail);
    setDraft(detail?.context.parsed ?? {});
    setEpisodesText((detail?.context.parsed?.episodes ?? []).join(', '));
    setTargetDir('');  // 空值 = 审批时按自动分类、重新整理时保持当前目录
    setChoosing(false);
  }

  const can = (action: TaskActionName) => allows(detail?.actions, action);
  const needDirs = can('approve') || can('reorganize');
  // 保存目录可能刚在设置里改过：每次打开需要选目录的任务都重新读取，不做页面级缓存
  const fetchedDirs = useAsync(
    () => (needDirs && !saveDirs ? inboxApi.saveDirs() : Promise.resolve(null)),
    [taskId, needDirs, Boolean(saveDirs)],
  );
  const dirs = saveDirs ?? fetchedDirs.data ?? null;

  const finish = (message: string) => {
    toast.success(message);
    onChanged?.();
    onClose();
  };

  const run = async (action: PostedAction, conflict?: ConflictChoice) => {
    if (!detail) return;
    setBusy(true);
    try {
      await taskApi.act(detail.task_id, ACTION_ENDPOINT[action], actionBody(action, { dir: targetDir, conflict }));
      finish(conflict ? CONFLICT_DONE[conflict] : ACTION_DONE[action]);
    } catch (err) {
      toast.error(errorMessage(err, '操作失败'));
    } finally {
      setBusy(false);
    }
  };

  const subscribe = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const { subscription, pending } = await subscriptionApi.addFromTask(detail.task_id);
      announceSubscription(subscription, pending, () => onChanged?.());
      onChanged?.();
      loaded.reload();
    } catch (err) {
      toast.error(errorMessage(err, '订阅失败'));
    } finally {
      setBusy(false);
    }
  };

  const saveParsed = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const next = await taskApi.correct(detail.task_id, {
        title: draft.title, media_type: draft.media_type,
        year: draft.year ? Number(draft.year) : null,
        season: draft.season == null ? null : Number(draft.season),
        episodes: parseEpisodes(episodesText), resolution: draft.resolution, reason: draft.reason,
      });
      loaded.mutate(() => next);
      toast.success('识别信息已保存');
      onChanged?.();
    } catch (err) {
      toast.error(errorMessage(err, '保存失败'));
    } finally {
      setBusy(false);
    }
  };

  const parsed = detail?.context.parsed;
  const meta = (detail?.context.meta ?? {}) as TaskMeta;
  const plan = meta.organize?.plan ?? [];
  const organizeError = meta.organize?.error;
  const partial = meta.storage?.partial;
  const conflict = can('resolve_conflict') ? meta.organize?.pending_conflict : undefined;
  const reorganize = () => (conflict ? setChoosing(true) : void run('reorganize'));

  const footer = detail && detail.actions.length ? (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap gap-2">
        {can('correct') ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void saveParsed()}>保存修改</Button> : null}
        {can('reparse') ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('reparse')}><RotateCcw />重新识别</Button> : null}
        {can('retry') ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('retry')}><RefreshCw />重试</Button> : null}
        {can('reconcile') ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('reconcile')}><ShieldCheck />核验转存</Button> : null}
        {can('accept_partial') ? (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('accept_partial')}>
            <Check />按已到的{partial ? ` ${partial.landed} 个` : '文件'}继续
          </Button>
        ) : null}
        {can('abandon') ? (
          <Button variant="outline" size="sm" disabled={busy} className="text-destructive"
            onClick={() => void confirmAbandon(parsed?.title, partial).then((ok) => { if (ok) void run('abandon'); })}>
            <Trash2 />放弃并删除
          </Button>
        ) : null}
        {can('reorganize') || conflict ? (
          <Button variant="outline" size="sm" disabled={busy} onClick={reorganize}><FolderTree />重新整理</Button>
        ) : null}
        {can('subscribe') ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void subscribe()}><BellPlus />追这部剧</Button> : null}
      </div>
      <div className="flex gap-2">
        {can('dismiss') ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('dismiss')} className="text-destructive"><EyeOff />忽略</Button> : null}
        {can('reject') ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void run('reject')} className="text-destructive"><X />忽略</Button> : null}
        {can('approve') ? <Button size="sm" disabled={busy} onClick={() => void run('approve')}><Check />确认转存</Button> : null}
      </div>
    </div>
  ) : null;

  const autoAlias = detail?.state === 'pending_approval' ? detail.auto_dir?.split('/')[0] : detail?.context.save_dir_alias;

  return (
    <Sheet
      open={taskId !== null}
      onOpenChange={(open) => { if (!open) onClose(); }}
      title={detail ? (parsed?.title || '尚未识别片名') : '任务详情'}
      description={detail ? `${detail.context.raw.source} · ${relativeTime(detail.created_at)}` : undefined}
      footer={footer}
    >
      {!detail ? (
        loaded.error ? (
          <ErrorState title="读取任务详情失败" error={loaded.error} onRetry={loaded.reload} retrying={loaded.loading} className="border-none" />
        ) : (
          <div className="space-y-3"><Skeleton className="h-32 w-full" /><Skeleton className="h-48 w-full" /></div>
        )
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
              {meta.confidence?.score !== undefined ? (
                <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  置信度 <span className="font-medium tabular-nums text-foreground">{Math.round(meta.confidence.score * 100)}%</span>
                  {(meta.confidence.caps ?? []).map((cap) => <ToneBadge key={cap} tone="warning">{CONFIDENCE_CAP_LABELS[cap] ?? cap}</ToneBadge>)}
                </div>
              ) : null}
              <div className="text-xs text-muted-foreground">
                {detail.context.save_dir_alias ? <>保存到「{detail.context.save_dir_alias}」 · </>
                  : detail.auto_dir ? <>将存到「{detail.auto_path ?? detail.auto_dir}」 · </> : null}
                <span title={fullTime(detail.updated_at)}>更新于 {relativeTime(detail.updated_at)}</span>
              </div>
            </div>
          </div>

          {detail.error || organizeError || detail.explanation ? (
            <div className="flex gap-2.5 rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <div className="min-w-0 space-y-1">
                <p className="break-words">{detail.explanation?.text ?? (organizeError || detail.error)}</p>
                {detail.explanation ? <p className="font-medium">建议：{detail.explanation.label}</p> : null}
                {detail.explanation && (organizeError || detail.error) ? (
                  <p className="break-words text-xs text-destructive/80">原始错误：{organizeError || detail.error}</p>
                ) : null}
              </div>
            </div>
          ) : null}

          {conflict ? (
            <Section title="库里已有同一份资源">
              <OrganizeConflict conflict={conflict} />
              <p className="text-xs leading-5 text-muted-foreground">点下方「重新整理」选择保留哪一份。</p>
              <ConflictDialog conflict={conflict} open={choosing} onOpenChange={setChoosing} canReplan={can('reorganize')}
                onChoose={(choice) => void (choice ? run('resolve_conflict', choice) : run('reorganize'))} />
            </Section>
          ) : null}

          {needDirs && dirs?.dirs.length && !conflict ? (
            <Section title="保存到">
              <Select value={targetDir} onValueChange={setTargetDir}
                options={dirs.dirs.map((d) => ({ value: d, label: dirs.paths[d] ? `${d}（${dirs.paths[d]}）` : d }))}
                placeholder={detail.state === 'pending_approval' ? `自动 · ${detail.auto_path ?? detail.auto_dir ?? '按分类'}`
                  : `保持当前目录「${detail.context.save_dir_alias ?? '默认'}」`}
                fallback={detail.state === 'pending_approval'
                  ? { value: autoAlias ?? '', label: `${detail.auto_path ?? detail.auto_dir} · 自动` }
                  : { value: autoAlias ?? '', note: '当前' }} />
              {targetDir && targetDir !== autoAlias
                ? <p className="mt-1.5 text-xs text-muted-foreground">直接存入「{dirs.paths[targetDir] ?? targetDir}」，不再套用分类子目录。</p> : null}
            </Section>
          ) : null}

          {can('correct') ? (
            <Section title="识别结果（可修改）">
              <div className="grid grid-cols-2 gap-3">
                <Field label="片名" className="col-span-2"><Input value={String(draft.title ?? '')} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field>
                <Field label="类型">
                  <Select value={String(draft.media_type ?? '')} onValueChange={(v) => setDraft({ ...draft, media_type: v || null })}
                    placeholder="未识别" options={MEDIA_TYPES} />
                </Field>
                <Field label="年份"><Input inputMode="numeric" value={String(draft.year ?? '')} onChange={(e) => setDraft({ ...draft, year: Number(e.target.value) || null })} /></Field>
                <Field label="季"><Input inputMode="numeric" value={String(draft.season ?? '')} onChange={(e) => setDraft({ ...draft, season: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
                <Field label="清晰度"><Input value={String(draft.resolution ?? '')} onChange={(e) => setDraft({ ...draft, resolution: e.target.value })} /></Field>
                <Field label="集号（逗号分隔）" className="col-span-2"><Input value={episodesText} onChange={(e) => setEpisodesText(e.target.value)} /></Field>
                <Field label="判断理由" className="col-span-2"><Textarea value={String(draft.reason ?? '')} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} className="min-h-16" /></Field>
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
