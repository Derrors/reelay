import { Monitor, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { Segmented } from './Segmented';

const OPTIONS = [
  { value: 'system', label: '跟随系统', icon: Monitor },
  { value: 'light', label: '浅色', icon: Sun },
  { value: 'dark', label: '深色', icon: Moon },
] as const;

/** 主题切换：跟随系统 / 浅色 / 深色。 */
export function ThemeToggle({ className }: { className?: string }) {
  // 纯客户端渲染：next-themes 首帧即可读到已保存的主题，不需要 mounted 守卫。
  const { theme, setTheme } = useTheme();
  return (
    <Segmented
      label="界面主题"
      value={theme ?? 'system'}
      onChange={setTheme}
      className={className}
      options={OPTIONS.map(({ value, label, icon: Icon }) => ({
        value, title: label, label: <><Icon className="size-3.5" /><span className="sr-only">{label}</span></>,
      }))}
    />
  );
}
