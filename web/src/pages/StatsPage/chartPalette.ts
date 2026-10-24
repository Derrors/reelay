/** 图表配色：只取主题变量，主题切换后重新取色。 */
import { useTheme } from 'next-themes';
import { useEffect, useState } from 'react';

export const TOKENS = [
  '--chart-1', '--chart-2', '--chart-3', '--chart-5',
  '--foreground', '--muted-foreground', '--border', '--card', '--popover', '--popover-foreground',
] as const;
export type Token = typeof TOKENS[number];
export interface ChartPalette { colors: Record<Token, string>; font: string }

/**
 * 读取当前主题下的颜色变量。变量是 `hsl(173 58% 38%)` 这种空格写法，zrender 解析不了（悬停高亮会变黑），
 * 所以借一个探针元素让浏览器换算成 rgb。
 */
function readPalette(): ChartPalette {
  const probe = document.createElement('span');
  probe.style.display = 'none';
  document.body.append(probe);
  const colors = Object.fromEntries(TOKENS.map((token) => {
    probe.style.color = `var(${token})`;
    return [token, getComputedStyle(probe).color];
  })) as Record<Token, string>;
  probe.remove();
  return { colors, font: getComputedStyle(document.body).fontFamily };
}

const samePalette = (a: ChartPalette, b: ChartPalette) =>
  a.font === b.font && TOKENS.every((token) => a.colors[token] === b.colors[token]);

/** 主题切换后重新取色。next-themes 在自己的 effect 里才改 <html class>，所以同时监听 class 变化。 */
export function useChartPalette(): ChartPalette {
  const { resolvedTheme } = useTheme();
  const [palette, setPalette] = useState(readPalette);
  useEffect(() => {
    const refresh = () => setPalette((prev) => {
      const next = readPalette();
      return samePalette(prev, next) ? prev : next;
    });
    const frame = requestAnimationFrame(refresh);
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [resolvedTheme]);
  return palette;
}

export function withAlpha(rgb: string, alpha: number): string {
  const [r = 0, g = 0, b = 0] = rgb.match(/[\d.]+/g)?.map(Number) ?? [];
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
