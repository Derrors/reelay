import { useEffect, useRef, useState } from 'react';

const DURATION = 420;
const easeOutQuart = (t: number) => 1 - (1 - t) ** 4;
const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** 数字变化时从旧值滚到新值（首次显示不动画）；系统开启「减少动态效果」时直接跳到新值。 */
export function useAnimatedNumber(value: number): number {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    if (start === value || reducedMotion()) {
      from.current = value;
      const frame = requestAnimationFrame(() => setShown(value));
      return () => cancelAnimationFrame(frame);
    }
    const began = performance.now();
    let frame = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - began) / DURATION);
      const next = start + (value - start) * easeOutQuart(t);
      from.current = next;
      setShown(next);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return shown;
}
