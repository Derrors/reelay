import { Bot, Film, PlugZap, Send, Sparkles } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import Auth115Card from '@/components/Auth115Card';
import { Panel } from '@/components/app/Panel';
import { ToneBadge } from '@/components/app/StatusBadge';
import { fullTime, relativeTime } from '@/lib/format';
import { connectionLabels } from '@/lib/status';
import type { IConnection } from '@/lib/types';
import { CONNECTION_TONE } from './shared';

const ICONS: Record<string, LucideIcon> = { telegram: Send, control_bot: Bot, llm: Sparkles, tmdb: Film };

export function ConnectionsTab({ connections }: { connections: Record<string, IConnection> }) {
  const others = Object.entries(connections).filter(([key]) => key !== 'p115');
  return (
    <div className="space-y-5">
      <Auth115Card />
      {others.length ? (
        <Panel icon={PlugZap} title="其他连接" description="已填写凭证不等于连接成功，以运行中实际观察到的状态为准。" bodyClassName="p-4 sm:p-5">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {others.map(([key, c]) => {
              const Icon = ICONS[key] ?? PlugZap;
              return (
                <div key={key} className="flex items-start gap-3 rounded-lg border border-border bg-background/60 p-3.5">
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                    <Icon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium leading-6">{c.label}</p>
                    <ToneBadge tone={CONNECTION_TONE[c.status] ?? 'neutral'} className="mt-1">
                      {connectionLabels[c.status] ?? '状态未知'}
                    </ToneBadge>
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      {c.observed_at
                        ? <span title={fullTime(c.observed_at)}>更新于 {relativeTime(c.observed_at)}</span>
                        : c.configured ? '已填写凭证' : '尚未填写凭证'}
                      {c.error ? <span className="block truncate text-destructive" title={c.error}>最近错误：{c.error}</span> : null}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
