import { CircleHelp, TriangleAlert } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { subscribeConfirm, type ConfirmRequest } from '@/lib/confirm';
import { cn } from '@/lib/utils';

/** 渲染 confirmDialog() 发起的确认；在 Layout 里挂一个即可。 */
export function ConfirmHost() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  // 关闭动画期间保留最后一次的内容，避免弹窗淡出时文字先消失
  const [shown, setShown] = useState<ConfirmRequest | null>(null);
  useEffect(() => subscribeConfirm((next) => {
    setRequest(next);
    if (next) setShown(next);
  }), []);

  const Icon = shown?.destructive ? TriangleAlert : CircleHelp;
  return (
    <Dialog open={request !== null} onOpenChange={(open) => { if (!open) request?.resolve(false); }}>
      <DialogContent showCloseButton={false} className="gap-5 p-5 sm:max-w-md">
        <div className="flex gap-3.5">
          <div className={cn('flex size-10 shrink-0 items-center justify-center rounded-full',
            shown?.destructive ? 'bg-destructive/10 text-destructive' : 'bg-primary/10 text-primary')}>
            <Icon className="size-5" />
          </div>
          <DialogHeader className="min-w-0 flex-1 gap-1.5 pt-1 text-left">
            <DialogTitle className="text-base leading-6">{shown?.title}</DialogTitle>
            {shown?.description ? (
              <DialogDescription className="text-[13px] leading-5">{shown.description}</DialogDescription>
            ) : null}
          </DialogHeader>
        </div>
        {shown?.details?.length ? (
          <dl className="space-y-2 rounded-lg border border-border bg-muted/50 p-3 text-xs">
            {shown.details.map((item) => (
              <div key={`${item.label}-${item.value}`} className="min-w-0">
                <dt className="text-muted-foreground">{item.label}</dt>
                <dd className="mt-0.5 break-all font-mono text-[12px] text-foreground">{item.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => request?.resolve(false)}>{shown?.cancelText ?? '取消'}</Button>
          <Button variant={shown?.destructive ? 'destructive' : 'default'} autoFocus onClick={() => request?.resolve(true)}>
            {shown?.confirmText ?? '确定'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
