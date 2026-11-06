import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AnimatedNumber } from '@/components/app/AnimatedNumber';
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
      'glass-panel group relative h-full rounded-xl p-4 shadow-card transition-[box-shadow,transform] duration-300 ease-(--ease-out-strong) sm:p-5',
      to && 'hover:-translate-y-0.5 hover:shadow-raised active:translate-y-0',
      highlight && 'shadow-[0_0_0_1px_color-mix(in_oklab,var(--warning)_35%,transparent),var(--shadow-sm)]',
    )}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-muted-foreground">{label}</span>
        <span className={cn('grid size-7 place-items-center rounded-md', style.soft, style.text)}>
          <Icon className="size-4" />
        </span>
      </div>
      <div className={cn('mt-3 text-[26px] font-semibold leading-none tracking-[-0.02em] tabular-nums', highlight && style.text)}>
        {typeof value === 'number' ? <AnimatedNumber value={value} /> : value}
      </div>
      {hint ? <div className="mt-2 text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  );
  return to ? <Link to={to} className="block rounded-xl focus-visible:outline-2 focus-visible:outline-offset-2">{body}</Link> : body;
}
