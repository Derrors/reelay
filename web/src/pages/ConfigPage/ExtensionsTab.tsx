import { useState, type Dispatch, type SetStateAction } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ChevronDown, Clock3, Puzzle, RotateCcw, Workflow, Zap } from 'lucide-react';
import PluginConfigEditor from '@/components/PluginConfigEditor';
import { EmptyState } from '@/components/app/EmptyState';
import { Field } from '@/components/app/Field';
import { Notice } from '@/components/app/Notice';
import { Panel } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { CHOICE_LABELS, SLOT_LABELS } from '@/lib/status';
import type { IExtension, IExtensionField } from '@/lib/types';
import { cn } from '@/lib/utils';
import { equal, getPath, numberOrBlank, obj, setPath, strings, text, type Obj } from '@/lib/values';
import { SectionHeading, SwitchRow } from './fields';
import { FlowOverview } from './FlowOverview';
import {
  PER_SOURCE_SLOTS, SLOT_ORDER, isEnabled, providerHint, providerLabel, stageNames, storagesInUse, type Catalog,
} from './shared';

interface ExtensionsTabProps {
  catalog: Catalog;
  hooks: IExtension[];
  events: IExtension[];
  config: Obj;
  saved: Obj;
  setConfig: Dispatch<SetStateAction<Obj>>;
  setField: (key: string, name: string, value: unknown) => void;
  busy: boolean;
  onToggle: (x: IExtension, enabled: boolean) => void;
  onRestore: (keys: string[]) => void;
}

const DirtyTag = () => (
  <span className="rounded-full bg-warning/12 px-1.5 text-[11px] font-medium leading-[18px] text-warning">未保存</span>
);

function SchemaField({ field, config, setConfig }: { field: IExtensionField; config: Obj; setConfig: Dispatch<SetStateAction<Obj>> }) {
  const value = getPath(config, field.path);
  // 未写入配置时后端实际使用的值（插件在 ui_fields 里声明）
  const fallback = field.default;
  const set = (v: unknown) => setConfig(c => setPath(c, field.path, v));
  if (field.type === 'boolean') {
    return <SwitchRow className="sm:col-span-2 bg-card" title={field.label} checked={Boolean(value ?? fallback)} onCheckedChange={set} />;
  }
  if (field.choices) {
    const shown = text(value ?? fallback);
    return (
      <Field label={field.label}>
        <Select
          value={shown} onValueChange={set}
          placeholder={field.choices.includes(shown) ? undefined : '请选择'}
          options={field.choices.map(c => ({ value: c, label: CHOICE_LABELS[c] ?? c }))}
        />
      </Field>
    );
  }
  const isNumber = field.type === 'number';
  const range = isNumber && (field.min !== undefined || field.max !== undefined)
    ? `可填 ${field.min ?? '不限'} ~ ${field.max ?? '不限'}` : undefined;
  return (
    <Field label={field.label} hint={range} className={isNumber ? undefined : 'sm:col-span-2'}>
      <Input
        className="bg-card" type={isNumber ? 'number' : 'text'} min={field.min} max={field.max}
        value={text(value)} placeholder={fallback === undefined ? undefined : `默认 ${text(fallback)}`}
        onChange={e => set(isNumber ? numberOrBlank(e.target.value) : e.target.value)}
      />
    </Field>
  );
}

interface ExtensionCardProps {
  x: IExtension;
  config: Obj;
  saved: Obj;
  setConfig: Dispatch<SetStateAction<Obj>>;
  busy: boolean;
  open: boolean;
  catalog: Catalog;
  onOpenChange: (open: boolean) => void;
  onToggle: (enabled: boolean) => void;
  onRestore: () => void;
}

function ExtensionCard({ x, config, saved, setConfig, busy, open, catalog, onOpenChange, onToggle, onRestore }: ExtensionCardProps) {
  const configurable = x.fields.length > 0 || Boolean(x.config_type);
  const enabled = isEnabled(x, obj(config.plugins));
  const dirty = !equal(getPath(config, x.config_section), getPath(saved, x.config_section));
  const mismatched = storagesInUse(config).filter(s => x.unsupported_storage.includes(s));
  const expanded = configurable && open;
  return (
    <article id={`ext-${x.name}`} tabIndex={-1} className={cn(
      'flex min-w-0 flex-col rounded-xl border border-card-border bg-card shadow-xs transition-colors',
      'outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/20',
      expanded && 'lg:col-span-2',
      dirty && 'border-warning/40',
    )}>
      <div className="flex items-start gap-4 p-4 sm:p-5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className={cn('text-[15px] font-semibold leading-6', !enabled && 'text-muted-foreground')}>{x.label}</h3>
            {dirty ? <DirtyTag /> : null}
          </div>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">{x.description}</p>
        </div>
        <Switch
          className="mt-1" aria-label={`${enabled ? '关闭' : '开启'}「${x.label}」`}
          checked={enabled} disabled={busy} onCheckedChange={onToggle}
        />
      </div>
      {mismatched.length ? (
        <Notice tone="warning" icon={AlertTriangle} className="mx-4 mb-4 rounded-lg py-2.5 sm:mx-5">
          不支持「{mismatched.map(s => providerLabel('storage', s, catalog)).join('、')}」，当前有规则使用它转存。请改用其他转存方式，或关闭此功能。
        </Notice>
      ) : null}
      <div className="mt-auto flex min-h-12 items-center justify-between gap-3 border-t border-border/70 px-4 py-2 sm:px-5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <span className={cn('size-1.5 rounded-full', enabled ? 'bg-success' : 'bg-muted-foreground/50')} />
            {enabled ? '已开启' : '已关闭'}
          </span>
          {stageNames(x.slot).length ? <span className="text-muted-foreground/40">·</span> : null}
          {stageNames(x.slot).map(stage => (
            <span key={stage} className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[11px]">
              <Clock3 className="size-3" />{stage}
            </span>
          ))}
        </div>
        {configurable ? (
          <Button variant="ghost" size="sm" className="-mr-2 shrink-0" aria-expanded={expanded} onClick={() => onOpenChange(!open)}>
            设置<ChevronDown className={cn('transition-transform', expanded && 'rotate-180')} />
          </Button>
        ) : null}
      </div>
      {expanded ? (
        <div className="space-y-4 rounded-b-xl border-t border-border/70 bg-muted/30 p-4 sm:p-5">
          {x.fields.length ? (
            <div className="grid gap-4 sm:grid-cols-2">
              {x.fields.map(f => <SchemaField key={f.path} field={f} config={config} setConfig={setConfig} />)}
            </div>
          ) : null}
          {x.config_type ? <PluginConfigEditor extension={x} config={config} setConfig={setConfig} /> : null}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/70 pt-3 text-xs text-muted-foreground">
            <span>{dirty ? '修改后点页面底部的「保存并生效」，只对新任务生效。' : '设置保存后只对新任务生效。'}</span>
            {dirty ? (
              <Button variant="ghost" size="sm" className="-mr-2" onClick={onRestore}><RotateCcw />撤销此项修改</Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}

export function ExtensionsTab({ catalog, hooks, events, config, saved, setConfig, setField, busy, onToggle, onRestore }: ExtensionsTabProps) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const providers = obj(config.providers);
  const defaults = obj(config.defaults);
  // 识别方式与转存方式按来源设置（设置 → 监听来源）；网盘只有一种时不必选
  const slots = [
    ...SLOT_ORDER.filter(s => catalog.providers[s] && !PER_SOURCE_SLOTS.includes(s)
      && (s !== 'storage' || (catalog.providers[s]?.length ?? 0) > 1)),
    ...Object.keys(catalog.providers).filter(s => !SLOT_ORDER.includes(s) && s !== 'notify'),
  ];
  const notify = catalog.providers.notify ?? [];
  const enabledNotify = strings(providers.notify);
  const slotValue = (slot: string) => text(slot === 'source' ? providers.source : defaults[slot]);
  const coreDirty = ['providers', 'defaults'].some(k => !equal(config[k], saved[k]));
  const loadErrors = catalog.load_errors ?? [];

  const card = (x: IExtension) => (
    <ExtensionCard
      key={`${x.kind}/${x.name}`}
      x={x} config={config} saved={saved} setConfig={setConfig} busy={busy} catalog={catalog}
      open={open.has(x.name)}
      onOpenChange={value => setOpen(prev => {
        const next = new Set(prev);
        if (value) next.add(x.name); else next.delete(x.name);
        return next;
      })}
      onToggle={enabled => onToggle(x, enabled)}
      onRestore={() => onRestore([x.config_section])}
    />
  );

  return (
    <div className="space-y-8">
      {loadErrors.length ? (
        <Notice tone="warning" icon={AlertTriangle} title={`有 ${loadErrors.length} 个扩展加载失败`}>
          <p>其他功能不受影响；修复后重启服务即可重新加载。</p>
          <ul className="mt-2 space-y-1.5">
            {loadErrors.map((e, i) => (
              <li key={i} className="rounded-md bg-card/70 px-3 py-2 text-xs leading-5">
                <span className="font-medium text-foreground">{e.source}</span>
                <span className="mt-0.5 block break-all font-mono text-muted-foreground">{e.error}</span>
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}

      <FlowOverview catalog={catalog} hooks={hooks} events={events} config={config} />

      <Panel
        icon={Workflow}
        title="核心能力"
        description={<>选择消息接入与通知方式。识别方式与转存方式按来源设置，在 <Link to="/config?tab=sources" className="text-primary hover:underline">设置 → 监听来源</Link> 的来源卡片上选择。</>}
        actions={coreDirty ? (
          <>
            <DirtyTag />
            <Button variant="ghost" size="sm" className="hidden sm:inline-flex" onClick={() => onRestore(['providers', 'defaults'])}>
              <RotateCcw />撤销
            </Button>
          </>
        ) : undefined}
        bodyClassName="p-4 sm:p-5"
      >
        <div className="grid gap-x-5 gap-y-4 md:grid-cols-2">
          {slots.map(slot => {
            const value = slotValue(slot);
            const entries = catalog.providers[slot] ?? [];
            const options = entries.map(x => ({ value: x.name, label: providerLabel(slot, x.name, catalog) }));
            if (value && !options.some(o => o.value === value)) options.push({ value, label: `${providerLabel(slot, value, catalog)}（未安装）` });
            return (
              <Field key={slot} label={SLOT_LABELS[slot] ?? providerLabel(slot, slot, catalog)} hint={providerHint(slot, value, catalog) || undefined}>
                <Select value={value} options={options} onValueChange={v => setField(slot === 'source' ? 'providers' : 'defaults', slot, v)} />
              </Field>
            );
          })}
        </div>
        {notify.length ? (
          <div className="mt-5 border-t border-border/70 pt-5">
            <p className="text-[13px] font-medium leading-5">通知渠道</p>
            <p className="mb-3 mt-0.5 text-xs text-muted-foreground">确认卡片与处理结果从这里发给你，可同时开启多个。</p>
            <div className="grid gap-2 md:grid-cols-2">
              {notify.map(x => (
                <SwitchRow
                  key={x.name}
                  title={providerLabel('notify', x.name, catalog)}
                  description={providerHint('notify', x.name, catalog)}
                  checked={enabledNotify.includes(x.name)}
                  onCheckedChange={v => setField('providers', 'notify', v ? [...enabledNotify, x.name] : enabledNotify.filter(n => n !== x.name))}
                />
              ))}
            </div>
          </div>
        ) : null}
      </Panel>

      <section className="space-y-4">
        <SectionHeading icon={Zap} title="自动处理" description="在任务流程的固定环节执行，按环节先后排列。开关立即生效。" />
        {hooks.length
          ? <div className="grid gap-4 lg:grid-cols-2">{hooks.map(card)}</div>
          : <EmptyState icon={Puzzle} title="没有已安装的处理插件" className="bg-card" />}
      </section>

      <section className="space-y-4">
        <SectionHeading icon={Clock3} title="后台事件" description="在特定事件发生时运行，不影响转存主流程。" />
        {events.length
          ? <div className="grid gap-4 lg:grid-cols-2">{events.map(card)}</div>
          : <EmptyState icon={Puzzle} title="没有已安装的事件插件" description="安装相关插件并重启服务后，会自动出现在这里。" className="bg-card" />}
      </section>
    </div>
  );
}
