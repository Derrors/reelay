import { AlertTriangle, BellPlus, Check, EyeOff, FolderTree, Lightbulb, ShieldCheck, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { MediaCover } from '@/components/app/MediaCover';
import { MediaMeta } from '@/components/app/MediaMeta';
import { Select } from '@/components/app/Select';
import { ToneBadge } from '@/components/app/StatusBadge';
import { ConflictButtons } from '@/components/OrganizeConflict';
import { Button } from '@/components/ui/button';
import { confirmAbandon, type ConflictChoice } from '@/lib/conflict';
import { fullTime, relativeTime } from '@/lib/format';
import { ADVICE_TONE, CONFIDENCE_CAP_LABELS, TONE_CLASS } from '@/lib/status';
import { allows, type PostedAction } from '@/lib/taskActions';
import type { IAdvice, IInboxItem, IShareProbe, TaskActionName } from '@/lib/types';
import { cn } from '@/lib/utils';
import { shareBadge } from './inbox';

/** 建议动作与理由：动作由规则根据事实决定，AI 只负责把理由写通顺。 */
function AdviceLine({ advice }: { advice: IAdvice }) {
  const tone = TONE_CLASS[ADVICE_TONE[advice.action]];
  return (
    <div className={cn('mt-2 flex items-start gap-2 rounded-lg px-2.5 py-1.5 text-xs leading-5', tone.soft)}>
      <Lightbulb className={cn('mt-0.5 size-3.5 shrink-0', tone.text)} />
      <span className="min-w-0">
        <b className={cn('font-medium', tone.text)}>{advice.label}</b>
        <span className="text-foreground/80">：{advice.text}</span>
        {advice.source === 'ai' ? <span className="ml-1.5 text-[10px] text-muted-foreground" title="由 AI 按同样的事实措辞">AI</span> : null}
      </span>
    </div>
  );
}

/** 读取分享的结果（见 shareBadge）；读取正常、集数来自消息时不显示。 */
export function ShareBadge({ share }: { share: IShareProbe | null }) {
  const badge = shareBadge(share);
  return badge ? <span title={badge.title}><ToneBadge tone={badge.tone}>{badge.label}</ToneBadge></span> : null;
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.8 ? 'bg-success' : value >= 0.5 ? 'bg-primary' : 'bg-warning';
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" title="由标题、类型、年份与 TMDB 匹配等信号计算">
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-muted">
        <span className={cn('block h-full rounded-full', tone)} style={{ width: `${Math.max(pct, 4)}%` }} />
      </span>
      <span className="tabular-nums">{pct}%</span>
    </span>
  );
}

interface InboxCardProps {
  item: IInboxItem;
  dirs: string[];
  /** 目录别名 → 网盘真实路径，用于在选项里注明位置。 */
  dirPaths: Record<string, string>;
  selected: boolean;
  busy: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onAct: (action: PostedAction, options?: { dir?: string; conflict?: ConflictChoice }) => void;
  onSubscribe: () => void;
}

/** 待审批 / 待人工的一张卡片；按钮只按服务端给出的 actions 显示。 */
export function InboxCard({ item, dirs, dirPaths, selected, busy, onToggle, onOpen, onAct, onSubscribe }: InboxCardProps) {
  const [dir, setDir] = useState('');  // 空值 = 按自动分类存放
  const can = (action: TaskActionName) => allows(item.actions, action);
  const manual = item.state === 'need_manual';
  const autoTarget = item.auto_path ?? item.auto_dir ?? '按分类';
  const autoAlias = item.auto_dir?.split('/')[0] ?? '';  // 不改目录时自动用的保存目录
  const share = shareBadge(item.share);
  const suggested = item.advice?.action;
  const ghostDanger = 'px-2 text-muted-foreground hover:text-destructive sm:px-3';
  return (
    <article className={cn(
      'group relative flex gap-4 rounded-xl border border-card-border bg-card p-4 shadow-xs transition-shadow hover:shadow-md',
      selected && 'border-primary/50 ring-1 ring-primary/30',
    )}>
      <button type="button" onClick={onOpen} className="w-14 shrink-0 self-start sm:w-[84px]" aria-label={`查看 ${item.title ?? '任务'} 详情`}>
        <MediaCover title={item.title} posterPath={item.poster_path} mediaType={item.media_type} quality="w185" />
      </button>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-start gap-2">
          <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left">
            <h3 className="truncate text-[15px] font-semibold leading-6 hover:text-primary">
              {item.title || '尚未识别片名'}
              {item.year ? <span className="ml-1.5 text-sm font-normal text-muted-foreground">{item.year}</span> : null}
            </h3>
          </button>
          {can('approve') || can('reject') ? (
            // 复选框外包一层，移动端可点区域不低于 36px
            <label className="-m-2.5 grid size-9 shrink-0 cursor-pointer place-items-center sm:-m-1 sm:size-6">
              <input type="checkbox" checked={selected} onChange={onToggle} aria-label="选择"
                className="size-4 cursor-pointer rounded accent-primary" />
            </label>
          ) : null}
        </div>
        <MediaMeta mediaType={item.media_type} season={item.season} episodes={item.episodes} pairs={item.episode_pairs}
          resolution={item.resolution} size={item.size} className="mt-0.5" />
        {item.release_tags?.length ? (
          <div className="mt-1 flex flex-wrap gap-1">
            {item.release_tags.map((tag) => (
              <span key={tag} className="rounded bg-muted px-1.5 text-[11px] leading-[18px] text-muted-foreground">{tag}</span>
            ))}
          </div>
        ) : null}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="min-w-0 truncate">{item.source} · <span title={fullTime(item.created_at)}>{relativeTime(item.created_at)}</span></span>
          {!manual ? <ConfidenceBar value={item.confidence} /> : null}
        </div>
        {!manual && (item.fallback || item.confidence_caps.length || share || item.version || item.subscribed) ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {item.version ? (
              <ToneBadge tone={item.version.best ? 'success' : 'neutral'}>
                {item.version.best ? `推荐版本 · 共 ${item.version.count} 个版本` : `另有更合适的版本（共 ${item.version.count} 个）`}
              </ToneBadge>
            ) : null}
            {item.subscribed ? <ToneBadge tone="info">已订阅</ToneBadge> : null}
            <ShareBadge share={item.share} />
            {item.fallback ? <ToneBadge tone="warning">AI 识别降级</ToneBadge> : null}
            {item.confidence_caps.filter((c) => c !== 'parse_fallback').map((cap) => (
              <ToneBadge key={cap} tone="warning">{CONFIDENCE_CAP_LABELS[cap] ?? cap}</ToneBadge>
            ))}
          </div>
        ) : null}
        {manual ? (
          <div className="mt-2 flex gap-2 rounded-lg bg-destructive/5 px-2.5 py-2 text-xs leading-5 text-destructive">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            {item.explanation ? (
              <span className="min-w-0 break-words">
                {item.explanation.text}
                <span className="mt-0.5 block font-medium">建议：{item.explanation.label}</span>
              </span>
            ) : (
              <span className="line-clamp-3 break-words">{item.organize_error || item.error || '需要人工确认处理结果'}</span>
            )}
          </div>
        ) : (
          <>
            {item.advice ? <AdviceLine advice={item.advice} /> : null}
            {item.preview ? (
              <p className="mt-2 line-clamp-2 whitespace-pre-line border-l-2 border-border pl-2 text-xs leading-5 text-muted-foreground">{item.preview}</p>
            ) : null}
          </>
        )}

        <div className={cn('mt-auto flex items-center justify-end gap-2 pt-3', manual && 'flex-wrap')}>
          {manual ? <Button size="sm" variant="ghost" onClick={onOpen}>详情</Button> : null}
          {can('approve') && dirs.length > 1 ? (
            <Select value={dir} onValueChange={setDir} aria-label="保存目录" className="mr-auto min-w-0 max-w-56 flex-1"
              title={dir && dir !== autoAlias ? `直接存入「${dirPaths[dir] ?? dir}」，不套用分类子目录`
                : `按分类存入「${autoTarget}」`}
              placeholder={`自动 · ${autoTarget}`} fallback={{ value: autoAlias, label: `${autoTarget} · 自动` }}
              options={dirs.map((d) => ({ value: d, label: dirPaths[d] ? `${d}（${dirPaths[d]}）` : d }))} />
          ) : null}
          {can('subscribe') ? (
            <Button size="sm" variant="ghost" disabled={busy} onClick={onSubscribe} title="订阅这部剧这一季，之后的新集自动转存"
              aria-label="追这部剧" className="shrink-0 px-2 text-muted-foreground hover:text-primary">
              <BellPlus /><span className="max-lg:hidden">追这部剧</span>
            </Button>
          ) : null}
          {can('abandon') ? (
            <Button size="sm" variant="ghost" disabled={busy} className={ghostDanger}
              onClick={() => void confirmAbandon(item.title, item.transfer_partial).then((ok) => { if (ok) onAct('abandon'); })}>
              <Trash2 />放弃
            </Button>
          ) : null}
          {can('dismiss') ? (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAct('dismiss')} className={ghostDanger}><EyeOff />忽略</Button>
          ) : null}
          {can('reconcile') ? <Button size="sm" variant="outline" disabled={busy} onClick={() => onAct('reconcile')}><ShieldCheck />核验转存</Button> : null}
          {can('accept_partial') ? (
            <Button size="sm" disabled={busy} onClick={() => onAct('accept_partial')}>
              <Check />按已到的{item.transfer_partial ? ` ${item.transfer_partial.landed} 个` : '文件'}继续
            </Button>
          ) : null}
          {can('resolve_conflict') && item.organize_conflict ? (
            <ConflictButtons conflict={item.organize_conflict} busy={busy}
              onChoose={(choice) => onAct('resolve_conflict', { conflict: choice })} />
          ) : can('reorganize') ? (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => onAct('reorganize')}><FolderTree />重新整理</Button>
          ) : null}
          {can('reject') ? (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAct('reject')} aria-label="忽略"
              className={cn(ghostDanger, suggested === 'reject' && 'bg-muted text-foreground')}>
              <X /><span className="max-sm:hidden">忽略</span>
            </Button>
          ) : null}
          {can('approve') ? (
            <Button size="sm" disabled={busy} onClick={() => onAct('approve', { dir: dir || undefined })}
              variant={suggested === 'reject' ? 'outline' : 'default'} className="shrink-0"><Check />转存</Button>
          ) : null}
        </div>
      </div>
    </article>
  );
}
