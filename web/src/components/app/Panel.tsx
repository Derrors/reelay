import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PanelProps {
  title?: ReactNode;
  description?: ReactNode;
  icon?: LucideIcon;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
}

/** 带标题栏的内容面板；没有标题时只是一张卡片。 */
export function Panel({ title, description, icon: Icon, actions, children, className, bodyClassName }: PanelProps) {
  return (
    <section className={cn('rounded-xl border border-card-border bg-card text-card-foreground shadow-xs', className)}>
      {title || actions ? (
        <div className="flex items-start justify-between gap-3 border-b border-border/70 px-4 py-3.5 sm:px-5 sm:py-4">
          <div className="flex min-w-0 items-start gap-3">
            {Icon ? (
              <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                <Icon className="size-4" />
              </span>
            ) : null}
            <div className="min-w-0">
              {title ? <h2 className="text-[15px] font-semibold leading-6">{title}</h2> : null}
              {description ? <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p> : null}
            </div>
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {children !== undefined ? <div className={cn('p-4 sm:p-5', bodyClassName)}>{children}</div> : null}
    </section>
  );
}
