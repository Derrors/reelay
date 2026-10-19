import { useState, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { TONE_CLASS, type Tone } from '@/lib/status';
import { cn } from '@/lib/utils';

/** 表单项：标签在上（13px 中粗），说明在下（12px 弱化）。 */
export function Field({ label, hint, children, className }: {
  label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <div className={cn('min-w-0 space-y-1.5', className)}>
      <label className="block space-y-1.5">
        <span className="block text-[13px] font-medium leading-5 text-foreground">{label}</span>
        {children}
      </label>
      {hint ? <p className="text-xs leading-5 text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** 一行说明 + 右侧开关，整行可点。 */
export function SwitchRow({ title, description, checked, onCheckedChange, disabled, className }: {
  title: ReactNode; description?: ReactNode; checked: boolean;
  onCheckedChange: (value: boolean) => void; disabled?: boolean; className?: string;
}) {
  return (
    <label className={cn(
      'flex min-h-14 cursor-pointer items-center justify-between gap-4 rounded-lg border border-border bg-background/60 px-4 py-3 transition-colors hover:bg-muted/50',
      disabled && 'cursor-not-allowed opacity-60',
      className,
    )}>
      <span className="min-w-0">
        <span className="block text-[13px] font-medium leading-5">{title}</span>
        {description ? <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">{description}</span> : null}
      </span>
      <Switch checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} />
    </label>
  );
}

/** 单选卡片组：选项带一句说明，比下拉框更易读。 */
export function RadioCards({ value, options, onChange, label }: {
  value: string; label: string;
  options: Array<{ value: string; label: string; hint?: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="grid gap-2">
      {options.map(option => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'flex w-full items-start gap-3 rounded-lg border border-border px-3.5 py-3 text-left transition-colors hover:bg-muted/50',
              'focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/20',
              active && 'border-primary/50 bg-primary/[0.06] hover:bg-primary/[0.08]',
            )}
          >
            <span className={cn(
              'mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border border-input bg-card',
              active && 'border-primary',
            )}>
              {active ? <span className="size-2 rounded-full bg-primary" /> : null}
            </span>
            <span className="min-w-0">
              <span className="block text-[13px] font-medium leading-5">{option.label}</span>
              {option.hint ? <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">{option.hint}</span> : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}

const cleanLines = (raw: string) => raw.split('\n').map(s => s.trim()).filter(Boolean);

/** 每行一个值的文本框：输入时保留空行，写回配置时去掉空行（后端不接受空字符串）。 */
export function LinesField({ value, onChange, placeholder, ariaLabel }: {
  value: string[]; onChange: (value: string[]) => void; placeholder?: string; ariaLabel?: string;
}) {
  const joined = value.join('\n');
  const [draft, setDraft] = useState(joined);
  const [synced, setSynced] = useState(joined);
  if (joined !== synced) {
    // 外部改动（如放弃修改）时同步草稿
    setSynced(joined);
    setDraft(joined);
  }
  return (
    <Textarea
      aria-label={ariaLabel}
      value={draft}
      placeholder={placeholder}
      className="min-h-20 bg-card"
      onChange={e => {
        const next = cleanLines(e.target.value);
        setDraft(e.target.value);
        setSynced(next.join('\n'));
        onChange(next);
      }}
    />
  );
}

/** 带色调的提示条（警告、错误、说明）。 */
export function Notice({ tone = 'info', icon: Icon, title, children, className, action }: {
  tone?: Tone; icon?: LucideIcon; title?: ReactNode; children?: ReactNode; className?: string; action?: ReactNode;
}) {
  const style = TONE_CLASS[tone];
  const border = {
    neutral: 'border-border', info: 'border-info/25', primary: 'border-primary/25',
    warning: 'border-warning/30', success: 'border-success/25', danger: 'border-destructive/30',
  }[tone];
  return (
    <div role={tone === 'danger' || tone === 'warning' ? 'alert' : undefined}
      className={cn('flex items-start gap-3 rounded-xl border px-4 py-3 text-sm', style.soft, border, className)}>
      {Icon ? <Icon className={cn('mt-0.5 size-4 shrink-0', style.text)} /> : null}
      <div className="min-w-0 flex-1">
        {title ? <p className={cn('font-medium leading-6', style.text)}>{title}</p> : null}
        {children ? <div className={cn('text-[13px] leading-6', title ? 'text-foreground/80' : style.text)}>{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/** 分组小标题（面板之间的分区）。 */
export function SectionHeading({ icon: Icon, title, description, action }: {
  icon?: LucideIcon; title: ReactNode; description?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex min-w-0 items-start gap-2.5">
        {Icon ? <Icon className="mt-1 size-4 shrink-0 text-primary" /> : null}
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold leading-6">{title}</h2>
          {description ? <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p> : null}
        </div>
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  );
}
