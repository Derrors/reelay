import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  side?: 'right' | 'left';
  className?: string;
}

/** 侧边抽屉：详情、移动端导航等需要保留上下文的场景使用。 */
export function Sheet({ open, onOpenChange, title, description, children, footer, side = 'right', className }: SheetProps) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
        <DialogPrimitive.Content
          className={cn(
            'fixed inset-y-0 z-50 flex w-full flex-col bg-background shadow-2xl outline-none duration-200',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            side === 'right'
              ? 'right-0 border-l data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right sm:max-w-xl'
              : 'left-0 border-r data-[state=open]:slide-in-from-left data-[state=closed]:slide-out-to-left sm:max-w-xs',
            className,
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b px-5 pb-4 pt-[max(1rem,env(safe-area-inset-top))]">
            <div className="min-w-0">
              <DialogPrimitive.Title className="text-base font-semibold leading-6">{title}</DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-0.5 text-xs text-muted-foreground">{description}</DialogPrimitive.Description>
              ) : <DialogPrimitive.Description className="sr-only">详情</DialogPrimitive.Description>}
            </div>
            <DialogPrimitive.Close className="-mr-1 grid size-9 shrink-0 sm:size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
              <X className="size-4" />
              <span className="sr-only">关闭</span>
            </DialogPrimitive.Close>
          </div>
          <div className={cn('min-h-0 flex-1 overflow-y-auto px-5 pt-5', footer ? 'pb-5' : 'pb-[calc(1.25rem+env(safe-area-inset-bottom))]')}>{children}</div>
          {footer ? <div className="border-t bg-card/60 px-5 pb-[calc(0.75rem+env(safe-area-inset-bottom))] pt-3">{footer}</div> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
