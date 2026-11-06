import { useState } from 'react';
import { cn } from '@/lib/utils';

/** 管理台背景：最近入库作品的 TMDB 剧照，大幅模糊后压一层底色，半透明的侧栏、导航与弹层透出它的颜色。
 *  毛玻璃关闭、系统要求减少透明度或没有剧照时不显示（CSS 里 .app-backdrop 会被隐藏）。 */
export function AppBackdrop({ path }: { path?: string | null }) {
  const [loaded, setLoaded] = useState<string | null>(null);
  if (!path) return null;
  return (
    <div aria-hidden className="app-backdrop pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <img key={path} src={`https://image.tmdb.org/t/p/w780${path}`} alt="" decoding="async"
        onLoad={() => setLoaded(path)}
        className={cn('size-full scale-125 object-cover blur-[60px] saturate-[1.6] transition-opacity duration-1000 ease-(--ease-out-strong)',
          loaded === path ? 'opacity-100' : 'opacity-0')} />
      <div className="absolute inset-0 bg-(--backdrop-veil)" />
    </div>
  );
}
