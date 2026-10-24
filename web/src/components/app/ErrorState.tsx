import { CircleAlert, LoaderCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { EmptyState } from './EmptyState';

/** 读取失败：说明原因并给出重试，不要显示成「还没有数据」或一直转圈。 */
export function ErrorState({ title, error, onRetry, retrying, className }: {
  title: string; error: string; onRetry: () => void; retrying?: boolean; className?: string;
}) {
  return (
    <EmptyState
      icon={CircleAlert}
      tone="danger"
      title={title}
      description={error}
      action={(
        <Button variant="outline" disabled={retrying} onClick={onRetry}>
          {retrying ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}重试
        </Button>
      )}
      className={className}
    />
  );
}

/** 已有数据时刷新失败：数据照常显示，上方一行提示。 */
export function RefreshError({ error, onRetry, className }: { error: string; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn('mb-4 flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-2.5 text-sm text-destructive', className)}>
      <span className="flex min-w-0 items-center gap-2"><CircleAlert className="size-4 shrink-0" />{error}</span>
      {onRetry ? <Button variant="outline" size="sm" className="shrink-0 bg-card" onClick={onRetry}>重试</Button> : null}
    </div>
  );
}
