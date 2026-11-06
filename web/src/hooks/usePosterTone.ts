import { useEffect, useState } from 'react';
import { loadPosterTone, type PosterTone } from '@/lib/posterTone';

/** 海报主色调；没有海报或读不到时为 null（调用方保持默认底色）。 */
export function usePosterTone(path: string | null | undefined): PosterTone | null {
  const [state, setState] = useState<{ path: string | null | undefined; tone: PosterTone | null }>({ path: null, tone: null });
  useEffect(() => {
    let alive = true;
    if (path) void loadPosterTone(path).then((tone) => { if (alive) setState({ path, tone }); });
    return () => { alive = false; };
  }, [path]);
  return state.path === path ? state.tone : null;
}

/** 卡片左上角铺一层很淡的海报色，向右下渐隐；没有色调时不铺。 */
export function toneWash(tone: PosterTone | null, alpha = 0.14): React.CSSProperties | undefined {
  if (!tone) return undefined;
  return { backgroundImage: `radial-gradient(130% 160% at 0% 0%, hsl(${tone.hue} ${tone.saturation}% 52% / ${alpha}), transparent 62%)` };
}
