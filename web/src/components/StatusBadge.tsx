import { Badge } from '@/components/ui/badge';
import type { TaskState } from '@/lib/types';
import { STATE_META } from '@/lib/status';
import { cn } from '@/lib/utils';

interface StatusBadgeProps {
  state: TaskState;
}

export default function StatusBadge({ state }: StatusBadgeProps) {
  const meta = STATE_META[state] ?? { label: state, className: '' };
  return (
    <Badge variant="outline" className={cn('no-default-hover-elevate font-normal', meta.className)}>
      {meta.label}
    </Badge>
  );
}
