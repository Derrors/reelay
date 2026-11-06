import { useTheme } from 'next-themes';
import { useEffect } from 'react';

const COLORS = { light: '#fafafb', dark: '#17181a' } as const;

/** 让地址栏 / 状态栏颜色跟随管理台当前的主题（含手动选的浅色、深色），而不只是跟随系统。 */
export function ThemeColor() {
  const { resolvedTheme } = useTheme();
  useEffect(() => {
    const color = COLORS[resolvedTheme === 'dark' ? 'dark' : 'light'];
    document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((meta) => {
      meta.content = color;
    });
  }, [resolvedTheme]);
  return null;
}
