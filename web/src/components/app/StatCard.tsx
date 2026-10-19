import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { TONE_CLASS, type Tone } from '@/lib/status';
import { cn } from '@/lib/utils';

interface StatCardProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon: LucideIcon;
  tone?: Tone;
  to?: string;
  highlight?: boolean;
}

/** 指标卡；传 to 时整卡可点击。highlight 用于需要立即处理的数字。 */
export function StatCard({ label, value, hint, icon: Icon, tone = 'neutral', to, highlight }: StatCardProps) {
  const style = TONE_CLASS[tone];
  const body = (
    <div className={cn(
      'group relative h-full rounded-xl border border-card-border bg-card p-4 shadow-xs transition-all sm:p-5',
      to && 'hover:-translate-y-px hover:border-primary/30 hover:shadow-md',
      highlight && 'border-warning/40 ring-1 ring-warning/20',
    )}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-muted-foreground">{label}</span>
        <span className={cn('grid size-8 place-items-center rounded-lg', style.soft, style.text)}>
          <Icon className="size-4" />
        </span>
      </div>
      <div className={cn('mt-3 text-[28px] font-semibold leading-none tracking-tight tabular-nums', highlight && style.text)}>{value}</div>
      {hint ? <div className="mt-2 text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  );
  return to ? <Link to={to} className="block rounded-xl focus-visible:outline-2 focus-visible:outline-offset-2">{body}</Link> : body;
}
