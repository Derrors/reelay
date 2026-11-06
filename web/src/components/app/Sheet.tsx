import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
import { Backdrop } from '@/components/app/Backdrop';
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
  /** TMDB 剧照：从抽屉最顶上铺下来，标题压在上面，向下渐隐。 */
  backdrop?: string | null;
}

/** 侧边抽屉：详情、移动端导航等需要保留上下文的场景使用。 */
export function Sheet({ open, onOpenChange, title, description, children, footer, side = 'right', className, backdrop }: SheetProps) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/25 backdrop-blur-[2px] dark:bg-black/50 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
        <DialogPrimitive.Content
          // 打开时聚焦抽屉本身而不是关闭按钮：屏幕阅读器照常进入，关闭按钮不会一打开就亮着焦点框
          onOpenAutoFocus={(e) => { e.preventDefault(); (e.currentTarget as HTMLElement).focus({ preventScroll: true }); }}
          className={cn(
            'glass-strong glass-blur fixed inset-y-0 z-50 flex w-full flex-col shadow-overlay outline-none duration-300 data-[state=open]:ease-(--ease-out-strong)',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            side === 'right'
              ? 'right-0 data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right sm:max-w-xl'
              : 'left-0 data-[state=open]:slide-in-from-left data-[state=closed]:slide-out-to-left sm:max-w-xs',
            className,
          )}
        >
          {backdrop ? <Backdrop path={backdrop} veil className="h-72" /> : null}
          <div className={cn('relative flex items-start justify-between gap-4 px-5 pb-4 pt-[max(1rem,env(safe-area-inset-top))]',
            !backdrop && 'border-b')}>
            <div className="min-w-0">
              <DialogPrimitive.Title className="text-base font-semibold leading-6">{title}</DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-0.5 text-xs text-muted-foreground">{description}</DialogPrimitive.Description>
              ) : <DialogPrimitive.Description className="sr-only">详情</DialogPrimitive.Description>}
            </div>
            <DialogPrimitive.Close className={cn('press -mr-1 grid size-9 shrink-0 sm:size-8 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground',
              backdrop && 'bg-background/60 backdrop-blur-sm')}>
              <X className="size-4" />
              <span className="sr-only">关闭</span>
            </DialogPrimitive.Close>
          </div>
          <div className={cn('relative min-h-0 flex-1 overflow-y-auto px-5 pt-5', footer ? 'pb-5' : 'pb-[calc(1.25rem+env(safe-area-inset-bottom))]')}>{children}</div>
          {footer ? <div className="relative border-t bg-card/60 px-5 pb-[calc(0.75rem+env(safe-area-inset-bottom))] pt-3">{footer}</div> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
