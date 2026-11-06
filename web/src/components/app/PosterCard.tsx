import { Check, Star } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { MediaCover } from '@/components/app/MediaCover';
import { mediaTypeLabel } from '@/lib/format';
import { cn } from '@/lib/utils';

const touchLike = () => typeof window !== 'undefined' && Boolean(window.matchMedia?.('(hover: none), (pointer: coarse)').matches);

/** 海报卡片（参照 MoviePilot）：悬停时浮起，底部渐变层显示简介与详情；左上角类型（窄海报不放）、右上角评分。
 *  触屏上第一次点展开详情，再点一次才打开。 */
export function PosterCard({ title, subtitle, posterPath, mediaType, rating, overview, details, done, quality = 'w342', onOpen, className }: {
  title: string; subtitle?: ReactNode; posterPath?: string | null; mediaType?: string | null;
  rating?: number | null; overview?: string | null;
  /** 悬停层里简介下面的几行（季集、大小、来源等）。 */
  details?: ReactNode;
  /** 已入库：右下角打勾。 */
  done?: boolean;
  quality?: 'w185' | 'w342';
  onOpen: () => void; className?: string;
}) {
  const [revealed, setRevealed] = useState(false);
  const open = () => {
    if (touchLike() && !revealed) { setRevealed(true); return; }
    onOpen();
  };
  return (
    <button type="button" onClick={open} onBlur={() => setRevealed(false)}
      className={cn('group min-w-0 text-left outline-none', className)}>
      <div className={cn(
        'relative overflow-hidden rounded-lg shadow-card transition-[transform,box-shadow] duration-300 ease-(--ease-out-strong)',
        'group-hover:-translate-y-1 group-hover:shadow-raised group-focus-visible:ring-[3px] group-focus-visible:ring-ring/30',
        revealed && '-translate-y-1 shadow-raised',
      )}>
        <MediaCover title={title} posterPath={posterPath} mediaType={mediaType} quality={quality} className="rounded-none ring-0" />
        {mediaType && quality !== 'w185' ? (
          <span className="absolute left-1.5 top-1.5 rounded-sm bg-black/55 px-1.5 text-[10.5px] font-medium leading-[18px] text-white backdrop-blur-sm">
            {mediaTypeLabel(mediaType)}
          </span>
        ) : null}
        {rating ? (
          <span className="absolute right-1.5 top-1.5 flex items-center gap-0.5 rounded-sm bg-black/55 px-1.5 text-[10.5px] font-semibold leading-[18px] tabular-nums text-white backdrop-blur-sm">
            <Star className="size-2.5 fill-current text-amber-300" />{rating.toFixed(1)}
          </span>
        ) : null}
        {done ? (
          <span className="absolute bottom-1.5 right-1.5 grid size-5 place-items-center rounded-full bg-success text-white shadow-sm transition-opacity duration-200 group-hover:opacity-0">
            <Check className="size-3" strokeWidth={3} />
          </span>
        ) : null}
        {overview || details ? (
          <div className={cn(
            'absolute inset-0 flex flex-col justify-end gap-1 bg-gradient-to-b from-black/10 via-black/55 to-black/90 p-2.5 text-white',
            'opacity-0 transition-opacity duration-300 ease-(--ease-out-strong) group-hover:opacity-100',
            revealed && 'opacity-100',
          )}>
            <span className="line-clamp-2 text-[13px] font-semibold leading-snug">{title}</span>
            {overview ? <span className="line-clamp-3 text-[11px] leading-4 text-white/80">{overview}</span> : null}
            {details ? <span className="text-[11px] leading-4 text-white/90">{details}</span> : null}
          </div>
        ) : null}
      </div>
      <div className="mt-2 truncate text-[13px] font-medium">{title}</div>
      {subtitle ? <div className="truncate text-xs text-muted-foreground">{subtitle}</div> : null}
    </button>
  );
}
