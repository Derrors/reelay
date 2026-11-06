import { useState } from 'react';
import { cn } from '@/lib/utils';

/** TMDB 剧照横幅：铺满容器，底部用遮罩淡出（露出容器自己的底色，半透明的抽屉也不会出现横边）。
 *  fade 是顶部压字用的底色（veil）。没有剧照或加载失败时不渲染。 */
const FADE_OUT = 'linear-gradient(to bottom, black 0%, black 40%, transparent 100%)';

export function Backdrop({ path, className, fade = 'var(--background)', shade, veil }: {
  path?: string | null; className?: string; fade?: string;
  /** 加一层暗化（剧照上压白字时用）。 */
  shade?: boolean;
  /** 顶部压一层渐隐的底色（剧照上压标题时用，深浅主题下标题都看得清）。 */
  veil?: boolean;
}) {
  const [state, setState] = useState<{ path?: string | null; status: 'loading' | 'loaded' | 'failed' }>({ path, status: 'loading' });
  const status = state.path === path ? state.status : 'loading';
  if (!path || status === 'failed') return null;
  return (
    <div aria-hidden className={cn('pointer-events-none absolute inset-x-0 top-0 overflow-hidden', className)}
      style={{ maskImage: FADE_OUT, WebkitMaskImage: FADE_OUT }}>
      <img src={`https://image.tmdb.org/t/p/w780${path}`} alt="" loading="lazy"
        onLoad={() => setState({ path, status: 'loaded' })} onError={() => setState({ path, status: 'failed' })}
        className={cn('size-full scale-[1.02] object-cover transition-opacity duration-700 ease-(--ease-out-strong)',
          status === 'loaded' ? 'opacity-100' : 'opacity-0')} />
      {shade ? <div className="absolute inset-0 bg-black/45" /> : null}
      {veil ? (
        <div className="absolute inset-0" style={{ background: `linear-gradient(to bottom, color-mix(in srgb, ${fade} 86%, transparent) 0%, color-mix(in srgb, ${fade} 50%, transparent) 20%, transparent 38%)` }} />
      ) : null}
    </div>
  );
}

/** 卡片右侧的剧照：向左渐隐，压在文字下面做背景。 */
export function SideBackdrop({ path, className }: { path?: string | null; className?: string }) {
  const [state, setState] = useState<{ path?: string | null; loaded: boolean; failed: boolean }>({ path, loaded: false, failed: false });
  const current = state.path === path ? state : { loaded: false, failed: false };
  if (!path || current.failed) return null;
  return (
    <img aria-hidden src={`https://image.tmdb.org/t/p/w780${path}`} alt="" loading="lazy"
      onLoad={() => setState({ path, loaded: true, failed: false })} onError={() => setState({ path, loaded: false, failed: true })}
      className={cn('pointer-events-none absolute inset-y-0 right-0 h-full w-3/5 object-cover transition-opacity duration-700',
        current.loaded ? 'opacity-30 dark:opacity-25' : 'opacity-0', className)}
      style={{ maskImage: 'linear-gradient(to left, black 10%, transparent 100%)', WebkitMaskImage: 'linear-gradient(to left, black 10%, transparent 100%)' }} />
  );
}
