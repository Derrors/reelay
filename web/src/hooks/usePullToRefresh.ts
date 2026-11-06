import { useEffect, useRef, useState } from 'react';
import { requestRefresh } from '@/lib/refresh';

const THRESHOLD = 64;
const MAX = 96;
const HOLD_MS = 700;

/** 手机下拉刷新：页面滚到顶时往下拉超过阈值松手，通知页面重取数据。返回下拉距离与是否正在刷新，用来画指示器。 */
export function usePullToRefresh(enabled: boolean) {
  const [pull, setPull] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const start = useRef<number | null>(null);
  const distance = useRef(0);

  useEffect(() => {
    if (!enabled) return undefined;
    const blocked = (target: EventTarget | null) =>
      target instanceof Element && Boolean(target.closest('[role="dialog"], input, textarea, select, [data-no-pull]'));
    const onStart = (e: TouchEvent) => {
      start.current = window.scrollY <= 0 && e.touches.length === 1 && !blocked(e.target) ? e.touches[0].clientY : null;
    };
    const onMove = (e: TouchEvent) => {
      if (start.current === null) return;
      const dy = e.touches[0].clientY - start.current;
      if (dy <= 0 || window.scrollY > 0) { distance.current = 0; setPull(0); return; }
      distance.current = Math.min(MAX, dy * 0.5); // 阻尼：手指走两步，指示器走一步
      setPull(distance.current);
    };
    const onEnd = () => {
      if (start.current === null) return;
      start.current = null;
      if (distance.current >= THRESHOLD) {
        setRefreshing(true);
        requestRefresh();
        window.setTimeout(() => setRefreshing(false), HOLD_MS);
      }
      distance.current = 0;
      setPull(0);
    };
    window.addEventListener('touchstart', onStart, { passive: true });
    window.addEventListener('touchmove', onMove, { passive: true });
    window.addEventListener('touchend', onEnd);
    window.addEventListener('touchcancel', onEnd);
    return () => {
      window.removeEventListener('touchstart', onStart);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('touchend', onEnd);
      window.removeEventListener('touchcancel', onEnd);
    };
  }, [enabled]);

  return { pull, refreshing, ready: pull >= THRESHOLD, threshold: THRESHOLD };
}
