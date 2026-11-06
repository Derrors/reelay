import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface SegmentedOption<T> {
  value: T;
  label: ReactNode;
  title?: string;
}

/** 分段切换（时间范围、类型筛选、显示方式等）：灰底一条，选中的白色滑块随选项滑动。
 *  size="md" 用在页面顶部的主筛选；fill 时各项平分整行。换行时滑块跟着换行。
 *  variant="plain" 没有灰底，选中项是浮起的白色胶囊（状态筛选用，参照 Filter Table）。 */
export function Segmented<T extends string | number>({ value, options, onChange, label, size = 'sm', fill, variant = 'track', className }: {
  value: T; options: Array<SegmentedOption<T>>; onChange: (value: NoInfer<T>) => void; label: string;
  size?: 'sm' | 'md'; fill?: boolean; variant?: 'track' | 'plain'; className?: string;
}) {
  const plain = variant === 'plain';
  const track = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [animate, setAnimate] = useState(false); // 首次定位不做动画，之后切换才滑动
  const index = options.findIndex((option) => option.value === value);

  useLayoutEffect(() => {
    const el = track.current;
    if (!el) return undefined;
    const place = () => {
      const button = el.querySelectorAll<HTMLElement>('[role="radio"]')[index];
      setThumb(button ? { x: button.offsetLeft, y: button.offsetTop, w: button.offsetWidth, h: button.offsetHeight } : null);
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(el);
    return () => observer.disconnect();
  }, [index, options.length]);
  useLayoutEffect(() => {
    if (thumb && !animate) {
      const frame = requestAnimationFrame(() => setAnimate(true));
      return () => cancelAnimationFrame(frame);
    }
    return undefined;
  }, [thumb, animate]);

  return (
    <div ref={track} role="radiogroup" aria-label={label}
      className={cn('relative isolate flex-wrap',
        plain ? 'gap-0.5' : 'rounded-lg bg-muted shadow-[inset_0_0_0_1px_var(--line)]',
        !plain && (size === 'md' ? 'p-1' : 'p-0.5'), fill ? 'flex' : 'inline-flex', className)}>
      {thumb ? (
        <span aria-hidden
          className={cn('absolute left-0 top-0 -z-10 bg-card shadow-btn dark:bg-hover-2', plain ? 'rounded-full' : 'rounded-md',
            animate && 'transition-[transform,width,height] duration-300 ease-(--ease-out-strong)')}
          style={{ width: thumb.w, height: thumb.h, transform: `translate(${thumb.x}px, ${thumb.y}px)` }} />
      ) : null}
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
            'inline-flex min-h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap font-medium transition-colors duration-200 sm:min-h-7',
            plain ? 'rounded-full' : 'rounded-md',
            size === 'md' ? 'px-3 text-[13px] sm:min-h-8' : 'px-2.5 text-xs',
            fill && 'flex-1',
            value === option.value ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** 分段按钮里的计数；有待处理的数时用 tone 标色。 */
export function SegmentCount({ count, tone }: { count: number; tone?: 'warning' | 'danger' }) {
  return (
    <span className={cn('min-w-4 text-center text-[11px] font-normal tabular-nums',
      count && tone === 'danger' ? 'text-destructive'
        : count && tone === 'warning' ? 'text-warning' : 'text-faint')}>
      {count}
    </span>
  );
}
