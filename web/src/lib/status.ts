// EXPORTS: STATE_META, formatDateTime
import type { TaskState } from '@/lib/types';

export const STATE_META: Record<TaskState, { label: string; className: string }> = {
  received: { label: '待解析', className: 'bg-info/10 text-info border-info/20' },
  parsed: { label: '已解析', className: 'bg-info/10 text-info border-info/20' },
  pending_approval: { label: '待审批', className: 'bg-warning/10 text-warning border-warning/20' },
  queued: { label: '待转存', className: 'bg-primary/10 text-primary border-primary/20' },
  rejected: { label: '已过滤', className: 'bg-muted text-muted-foreground border-border' },
  skipped: { label: '已忽略', className: 'bg-muted text-muted-foreground border-border' },
  saving: { label: '处理中', className: 'bg-primary/10 text-primary border-primary/20' },
  need_manual: { label: '待整理', className: 'bg-warning/10 text-warning border-warning/20' },
  done: { label: '已完成', className: 'bg-success/10 text-success border-success/20' },
  failed: { label: '失败', className: 'bg-destructive/10 text-destructive border-destructive/20' },
};

export function formatDateTime(value: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value * 1000));
}
