import { Monitor, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { cn } from '@/lib/utils';

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
    <div role="radiogroup" aria-label="界面主题" className={cn('inline-flex rounded-lg bg-muted p-0.5', className)}>
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            title={label}
            onClick={() => setTheme(value)}
            className={cn(
              'grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:text-foreground',
              active && 'bg-card text-foreground shadow-xs',
            )}
          >
            <Icon className="size-3.5" />
            <span className="sr-only">{label}</span>
          </button>
        );
      })}
    </div>
  );
}
