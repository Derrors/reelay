import { useSyncExternalStore } from 'react';
import { glassEnabled, subscribeGlass } from '@/lib/appearance';
import { useMediaQuery } from '@/hooks/useMediaQuery';

/** 毛玻璃是否开着（用户开关），以及系统是否要求减少透明度（此时界面自动用实色）。 */
export function useGlass() {
  const enabled = useSyncExternalStore(subscribeGlass, glassEnabled, () => true);
  const reduced = useMediaQuery('(prefers-reduced-transparency: reduce)');
  return { enabled, reduced, active: enabled && !reduced };
}
