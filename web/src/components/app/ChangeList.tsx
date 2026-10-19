import { Minus, PencilLine, Plus } from 'lucide-react';
import type { IRuleChange } from '@/api/ai';
import { cn } from '@/lib/utils';

const KIND = {
  add: { icon: Plus, className: 'text-success' },
  remove: { icon: Minus, className: 'text-destructive' },
  change: { icon: PencilLine, className: 'text-info' },
} as const;

/** 规则改动清单，按分区分组。 */
export function ChangeList({ changes }: { changes: IRuleChange[] }) {
  const sections = [...new Set(changes.map((c) => c.section))];
  return (
    <div className="space-y-3">
      {sections.map((section) => (
        <div key={section}>
          <p className="mb-1 text-xs font-medium text-muted-foreground">{section}</p>
          <ul className="space-y-1">
            {changes.filter((c) => c.section === section).map((c) => {
              const { icon: Icon, className } = KIND[c.kind];
              return (
                <li key={`${c.kind}-${c.text}`} className="flex items-start gap-2 text-[13px] leading-5">
                  <Icon className={cn('mt-0.5 size-3.5 shrink-0', className)} />
                  <span className="min-w-0 break-words">{c.text}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}
