import { Clapperboard, Tv } from 'lucide-react';
import { useState } from 'react';
import { hueOf, posterUrl } from '@/lib/format';
import { cn } from '@/lib/utils';

interface MediaCoverProps {
  title?: string | null;
  posterPath?: string | null;
  mediaType?: string | null;
  className?: string;
  /** 海报尺寸：小缩略图用 w185，卡片用 w342。 */
  quality?: 'w185' | 'w342' | 'w500';
}

/** 2:3 海报；没有 TMDB 海报或加载失败时，用片名生成配色稳定的文字封面。 */
export function MediaCover({ title, posterPath, mediaType, className, quality = 'w342' }: MediaCoverProps) {
  const [failed, setFailed] = useState(false);
  const url = failed ? null : posterUrl(posterPath, quality);
  const name = (title || '未识别').trim();
  const hue = hueOf(name);
  const Icon = mediaType === 'movie' ? Clapperboard : Tv;
  return (
    <div className={cn('relative aspect-[2/3] w-full overflow-hidden rounded-lg bg-muted ring-1 ring-black/5 dark:ring-white/10', className)}>
      {url ? (
        <img src={url} alt={name} loading="lazy" onError={() => setFailed(true)} className="size-full object-cover" />
      ) : (
        <div
          aria-label={name}
          role="img"
          className="@container flex size-full flex-col justify-between p-[9%] text-white"
          style={{ background: `linear-gradient(150deg, hsl(${hue} 42% 42%), hsl(${(hue + 40) % 360} 46% 24%))` }}
        >
          {/* 窄缩略图只显示首字，较大的封面显示图标与完整片名 */}
          <Icon className="hidden size-[16cqw] opacity-70 @[4.5rem]:block" />
          <div className="hidden @[4.5rem]:block">
            <span className="line-clamp-3 text-[13cqw] font-semibold leading-tight drop-shadow-sm">{name}</span>
          </div>
          <span className="m-auto text-[52cqw] font-semibold leading-none drop-shadow-sm @[4.5rem]:hidden">
            {Array.from(name)[0]}
          </span>
        </div>
      )}
    </div>
  );
}
