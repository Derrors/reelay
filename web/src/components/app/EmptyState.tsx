import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function EmptyState({ icon: Icon, title, description, action, className, tone = 'primary' }: {
  icon: LucideIcon; title: string; description?: ReactNode; action?: ReactNode; className?: string;
  /** danger 用于读取失败。 */
  tone?: 'primary' | 'danger';
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-14 text-center', className)}>
      <span className={cn('grid size-11 place-items-center rounded-full',
        tone === 'danger' ? 'bg-destructive/10 text-destructive' : 'bg-primary/10 text-primary')}>
        <Icon className="size-5" />
      </span>
      <p className="mt-4 text-sm font-medium">{title}</p>
      {description ? <p className="mt-1 max-w-sm break-words text-xs leading-5 text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
