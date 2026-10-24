import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface SegmentedOption<T> {
  value: T;
  label: ReactNode;
  title?: string;
}

/** 分段切换（时间范围、类型筛选、显示方式等）：底色一条，选中项浮起。
 *  size="md" 用在页面顶部的主筛选；fill 时各项平分整行。 */
export function Segmented<T extends string | number>({ value, options, onChange, label, size = 'sm', fill, className }: {
  value: T; options: Array<SegmentedOption<T>>; onChange: (value: NoInfer<T>) => void; label: string;
  size?: 'sm' | 'md'; fill?: boolean; className?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label}
      className={cn('flex-wrap rounded-lg bg-muted', size === 'md' ? 'p-1' : 'p-0.5', fill ? 'flex' : 'inline-flex', className)}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          title={option.title}
          onClick={() => onChange(option.value)}
          className={cn(
            // 移动端可点区域不低于 36px
            'inline-flex min-h-9 items-center justify-center gap-1.5 rounded-md font-medium transition-colors sm:min-h-8',
            size === 'md' ? 'px-3 text-sm' : 'px-2.5 text-xs',
            fill && 'flex-1',
            value === option.value ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** 分段按钮里的计数角标；有待处理的数时用 tone 标色。 */
export function SegmentCount({ count, tone }: { count: number; tone?: 'warning' | 'danger' }) {
  return (
    <span className={cn('rounded-full px-1.5 text-[11px] tabular-nums',
      count && tone === 'danger' ? 'bg-destructive/10 text-destructive'
        : count && tone === 'warning' ? 'bg-warning/12 text-warning' : 'bg-background text-muted-foreground')}>
      {count}
    </span>
  );
}
