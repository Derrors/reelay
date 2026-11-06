import { useAnimatedNumber } from '@/hooks/useAnimatedNumber';

/** 变化时滚动过去的数字；decimals 为小数位数。 */
export function AnimatedNumber({ value, decimals = 0 }: { value: number; decimals?: number }) {
  const shown = useAnimatedNumber(value);
  return <>{decimals ? shown.toFixed(decimals) : Math.round(shown).toLocaleString('zh-CN')}</>;
}
