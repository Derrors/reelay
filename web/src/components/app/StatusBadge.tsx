import { ACTIVE_STATES, STATE_META, TONE_CLASS, type Tone } from '@/lib/status';
import type { TaskState } from '@/lib/types';
import { cn } from '@/lib/utils';

export function ToneBadge({ tone, children, className, pulse = false }: {
  tone: Tone; children: React.ReactNode; className?: string; pulse?: boolean;
}) {
  const style = TONE_CLASS[tone];
  return (
    <span className={cn('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium', style.badge, className)}>
      <span className={cn('size-1.5 shrink-0 rounded-full', style.dot, pulse && 'animate-pulse')} />
      {children}
    </span>
  );
}

/** 任务状态徽标；进行中的状态带呼吸点。 */
export function StatusBadge({ state, className }: { state: TaskState; className?: string }) {
  const meta = STATE_META[state];
  return <ToneBadge tone={meta.tone} pulse={ACTIVE_STATES.includes(state)} className={className}>{meta.label}</ToneBadge>;
}
