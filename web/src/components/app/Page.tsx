import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PageProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** 统一的页面外壳：固定最大宽度、页头与边距；底部留白避开移动端标签栏。 */
export function Page({ title, description, actions, children, className }: PageProps) {
  return (
    <div className={cn('mx-auto w-full max-w-[1280px] px-4 pb-28 pt-5 sm:px-6 lg:px-10 lg:pb-14 lg:pt-9', className)}>
      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold tracking-tight text-foreground sm:text-2xl">{title}</h1>
          {description ? <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </header>
      {children}
    </div>
  );
}
