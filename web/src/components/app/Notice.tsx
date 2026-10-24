import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { TONE_CLASS, type Tone } from '@/lib/status';
import { cn } from '@/lib/utils';

const BORDER: Record<Tone, string> = {
  neutral: 'border-border', info: 'border-info/25', primary: 'border-primary/25',
  warning: 'border-warning/30', success: 'border-success/25', danger: 'border-destructive/30',
};

/** 带色调的提示条（警告、错误、说明）。 */
export function Notice({ tone = 'info', icon: Icon, title, children, className, action }: {
  tone?: Tone; icon?: LucideIcon; title?: ReactNode; children?: ReactNode; className?: string; action?: ReactNode;
}) {
  const style = TONE_CLASS[tone];
  return (
    <div role={tone === 'danger' || tone === 'warning' ? 'alert' : undefined}
      className={cn('flex items-start gap-3 rounded-xl border px-4 py-3 text-sm', style.soft, BORDER[tone], className)}>
      {Icon ? <Icon className={cn('mt-0.5 size-4 shrink-0', style.text)} /> : null}
      <div className="min-w-0 flex-1">
        {title ? <p className={cn('font-medium leading-6', style.text)}>{title}</p> : null}
        {children ? <div className={cn('text-[13px] leading-6', title ? 'text-foreground/80' : style.text)}>{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
