import { episodeLabel, mediaTypeLabel } from '@/lib/format';
import { cn } from '@/lib/utils';

interface MediaMetaProps {
  mediaType?: string | null;
  season?: number | null;
  episodes?: number[] | null;
  /** 跨多季合集的（季, 集），有值时显示「S01–S03 · 30 集」。 */
  pairs?: number[][] | null;
  resolution?: string | null;
  size?: string | null;
  showType?: boolean;
  className?: string;
}

/** 媒体信息行：类型 · 季集 · 清晰度 · 大小，缺失的项自动省略。 */
export function MediaMeta({ mediaType, season, episodes, pairs, resolution, size, showType = true, className }: MediaMetaProps) {
  const parts = [
    showType && mediaType ? mediaTypeLabel(mediaType) : '',
    mediaType === 'movie' ? '' : episodeLabel(season, episodes, pairs),
    resolution ?? '',
    size ?? '',
  ].filter(Boolean);
  if (!parts.length) return null;
  return (
    <div className={cn('flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground', className)}>
      {parts.map((part, i) => (
        // 分隔点挂在前一项末尾，换行时不会出现在行首
        <span key={i} className="inline-flex items-center gap-1.5">
          <span className={cn(i === 0 && showType ? 'text-foreground/70' : '', 'tabular-nums')}>{part}</span>
          {i < parts.length - 1 ? <span className="text-muted-foreground/40">·</span> : null}
        </span>
      ))}
    </div>
  );
}
