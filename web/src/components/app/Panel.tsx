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

/** 两列面板：每列各自往下排，矮面板下面不留空；窄屏合成一列，先左后右。
 *  两张以上面板并排时用它，不要用按行对齐的 grid，否则同一行里矮的那张下面会空出一块。 */
export function PanelColumns({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
      <div className="grid min-w-0 gap-5">{left}</div>
      <div className="grid min-w-0 gap-5">{right}</div>
    </div>
  );
}
