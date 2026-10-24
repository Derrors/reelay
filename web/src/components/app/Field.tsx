import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** 表单项：标签在上（13px 中粗），说明在下（12px 弱化）；extra 放在标签行右侧（如计数、状态）。 */
export function Field({ label, hint, extra, children, className }: {
  label: ReactNode; hint?: ReactNode; extra?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <div className={cn('min-w-0 space-y-1.5', className)}>
      <label className="block space-y-1.5">
        <span className="flex min-h-5 items-center justify-between gap-2 text-[13px] font-medium leading-5 text-foreground">
          {label}
          {extra ? <span className="font-normal">{extra}</span> : null}
        </span>
        {children}
      </label>
      {hint ? <p className="text-xs leading-5 text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
