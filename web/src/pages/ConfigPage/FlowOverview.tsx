import type { ReactNode } from 'react';
import { ArrowDown, ArrowRight, CheckCircle2, Clock3, Waypoints } from 'lucide-react';
import { Panel } from '@/components/app/Panel';
import type { IExtension } from '@/lib/types';
import { cn } from '@/lib/utils';
import { LAYOUT_LABELS } from '@/plugins/organizer/layouts';
import {
  PER_SOURCE_SLOTS, STAGE_LABELS, isEnabled, obj, providerLabel, rows, stageNames, strings, text, type Catalog, type Obj,
} from './shared';

interface FlowOverviewProps {
  catalog: Catalog;
  hooks: IExtension[];
  events: IExtension[];
  config: Obj;
}

const slotsOf = (x: IExtension) => x.slot.split(',').map(s => s.trim()).filter(Boolean);
const HOOK_STAGES = ['after_parse', 'before_save', 'after_save'];

/** 点插件跳到下方它的设置卡片。 */
function jumpTo(name: string) {
  const el = document.getElementById(`ext-${name}`);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.focus({ preventScroll: true });
}

function CoreNode({ caption, label, tone = 'primary' }: { caption: string; label: string; tone?: 'primary' | 'success' }) {
  return (
    <div className={cn(
      'rounded-lg border px-3 py-2.5',
      tone === 'success' ? 'border-success/30 bg-success/8' : 'border-primary/30 bg-primary/8',
    )}>
      <p className={cn('flex items-center gap-1 text-[11px] font-medium', tone === 'success' ? 'text-success' : 'text-primary')}>
        {tone === 'success' ? <CheckCircle2 className="size-3" /> : null}{caption}
      </p>
      <p className="mt-0.5 truncate text-[13px] font-medium text-foreground" title={label}>{label || '未选择'}</p>
    </div>
  );
}

function PluginNode({ x, enabled, meta }: { x: IExtension; enabled: boolean; meta?: string }) {
  return (
    <button
      type="button"
      onClick={() => jumpTo(x.name)}
      title={`${x.label}：${enabled ? '已开启' : '已关闭'}，点击查看设置`}
      className={cn(
        'flex w-full min-w-0 items-center gap-2 rounded-lg border bg-card px-2.5 py-1.5 text-left text-xs transition-colors',
        'hover:border-primary/40 hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-ring',
        enabled ? 'border-border text-foreground' : 'border-dashed border-border text-muted-foreground',
      )}
    >
      <span className={cn('size-1.5 shrink-0 rounded-full', enabled ? 'bg-success' : 'bg-muted-foreground/40')} />
      <span className={cn('min-w-0 flex-1 truncate', !enabled && 'line-through decoration-muted-foreground/40')}>{x.label}</span>
      {meta ? <span className="shrink-0 text-[11px] text-muted-foreground">{meta}</span> : null}
    </button>
  );
}

/** 挂在某个环节上的一组插件：左侧虚线表示「附着」在主流程的这一步。 */
function Attached({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5 border-l-2 border-dashed border-border pl-2.5">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

function Step({ index, title, node, children }: { index: number; title: string; node: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2.5">
      <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <span className="grid size-5 place-items-center rounded-full bg-muted text-[11px] tabular-nums text-foreground">{index}</span>
        {title}
      </p>
      {node}
      {children}
    </div>
  );
}

const Arrow = () => (
  // 与各环节的核心节点对齐：标题行 + 节点上半部分
  <div className="flex shrink-0 justify-center text-muted-foreground/50 lg:pt-[42px]" aria-hidden>
    <ArrowDown className="size-4 lg:hidden" />
    <ArrowRight className="hidden size-4 lg:block" />
  </div>
);

/** 插件页顶部的运行概览：主流程各环节用的核心能力，以及插件挂在哪一步。全部来自当前配置。 */
export function FlowOverview({ catalog, hooks, events, config }: FlowOverviewProps) {
  const plugins = obj(config.plugins);
  const providers = obj(config.providers);
  const defaults = obj(config.defaults);
  const sources = rows(config.sources);
  const core = (slot: string) => {
    if (!PER_SOURCE_SLOTS.includes(slot) || !sources.length) {
      return providerLabel(slot, text(slot === 'source' ? providers.source : defaults[slot]), catalog);
    }
    // 识别与判断按来源设置：各来源一致时写出来，不一致时提示按来源
    const used = new Set(sources.map(s => text(s[slot] || defaults[slot])));
    return used.size === 1 ? providerLabel(slot, [...used][0], catalog) : '按来源设置';
  };
  const notify = strings(providers.notify).map(n => providerLabel('notify', n, catalog));

  const at = (stage: string) => hooks.filter(x => slotsOf(x).includes(stage));
  // 不认识的环节（外部插件）放在最后，至少能在图上看到
  const other = hooks.filter(x => !slotsOf(x).some(s => HOOK_STAGES.includes(s)));
  // 媒体库整理在转存前选目录：标出当前的目录方式
  const layout = LAYOUT_LABELS[text(obj(plugins.organizer).layout)];
  const metaAt = (stage: string, x: IExtension) => (stage === 'before_save' && x.name === 'organizer' ? layout : undefined);
  const nodes = (items: IExtension[], stage = '') =>
    items.map(x => <PluginNode key={x.name} x={x} enabled={isEnabled(x, plugins)} meta={metaAt(stage, x)} />);
  const group = (stage: string, items = at(stage)) =>
    items.length ? <Attached label={STAGE_LABELS[stage] ?? stage}>{nodes(items, stage)}</Attached> : null;

  return (
    <Panel
      icon={Waypoints}
      title="运行概览"
      description="一条消息从接收到入库经过的环节，以及各插件挂在哪一步；点插件可跳到下方查看设置。"
      bodyClassName="p-4 sm:p-5"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:gap-2">
        <Step index={1} title="接收" node={<CoreNode caption="消息接入" label={core('source')} />}>
          <p className="text-[11px] leading-4 text-muted-foreground">消息先落库，再进入识别</p>
        </Step>
        <Arrow />
        <Step index={2} title="识别" node={<CoreNode caption="识别方式" label={core('parser')} />}>
          {group('after_parse')}
        </Step>
        <Arrow />
        <Step index={3} title="判断" node={<CoreNode caption="转存判断" label={core('decision')} />}>
          <ul className="space-y-1 text-[11px] leading-4 text-muted-foreground">
            <li>放行 → 转存</li>
            <li>拿不准 → 等你确认</li>
            <li>广告 / 已入库 → 跳过</li>
            {notify.length ? <li className="pt-1 text-muted-foreground/80">确认卡片经「{notify.join('、')}」发给你</li> : null}
          </ul>
        </Step>
        <Arrow />
        <Step index={4} title="转存" node={<CoreNode caption="网盘" label={core('storage')} />}>
          {group('before_save')}
          {group('after_save')}
          {other.length ? <Attached label="其他环节">{nodes(other)}</Attached> : null}
        </Step>
        <Arrow />
        <Step index={5} title="入库" node={<CoreNode caption="完成" label="读回核验通过" tone="success" />}>
          <p className="text-[11px] leading-4 text-muted-foreground">按 Jellyfin / Emby 规范命名归类</p>
        </Step>
      </div>

      {events.length ? (
        <div className="mt-5 border-t border-border/70 pt-4">
          <p className="mb-2.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Clock3 className="size-3.5" />后台事件 · 旁路运行，失败不影响主流程
          </p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {events.map(x => <PluginNode key={x.name} x={x} enabled={isEnabled(x, plugins)} meta={stageNames(x.slot).join(' · ')} />)}
          </div>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-[3px] border border-primary/30 bg-primary/8" />核心能力（识别与判断在来源卡片上选，其余在下方「核心能力」）</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-1.5 rounded-full bg-success" />插件已开启</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-[3px] border border-dashed border-muted-foreground/50" />插件已关闭</span>
      </div>
    </Panel>
  );
}
