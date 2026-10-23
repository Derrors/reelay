import { useEffect, useState } from 'react';
import { ChevronDown, Plus, Radio, SlidersHorizontal, Sparkles, Trash2, TrendingUp } from 'lucide-react';
import { ai, percent, type ISourceInsight } from '@/api/ai';
import { EmptyState } from '@/components/app/EmptyState';
import { Panel } from '@/components/app/Panel';
import { Select, type SelectOption } from '@/components/app/Select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { approvalLabels, approvalShortLabels } from '@/lib/status';
import { cn } from '@/lib/utils';
import { Field, LinesField, Notice } from './fields';
import { SourceWizard } from './SourceWizard';
import {
  APPROVAL_OPTIONS, SLOT_LABELS, obj, providerLabel, prune, rows, strings, text, type Catalog, type Obj,
} from './shared';

const ENGINE_SLOTS = ['parser', 'decision', 'storage'];

/** 当前值不在可选项里（目录被删、扩展被卸载）时补一项，避免下拉框悄悄显示成别的值。 */
function withCurrent(options: SelectOption[], value: string): SelectOption[] {
  return !value || options.some(o => o.value === value) ? options : [...options, { value, label: `${value}（已失效）` }];
}

interface SourceCardProps {
  index: number;
  source: Obj;
  defaults: Obj;
  aliases: string[];
  catalog: Catalog | null;
  open: boolean;
  /** 人工裁决统计与「改为自动转存」建议。 */
  insight?: ISourceInsight;
  onToggle: () => void;
  onChange: (patch: Obj) => void;
  onRemove: () => void;
}

function SourceCard({ index, source, defaults, aliases, catalog, open, insight, onToggle, onChange, onRemove }: SourceCardProps) {
  const name = text(source.bot).trim();
  const filters = obj(source.filters);
  const mode = text(source.approval_mode || defaults.approval_mode);
  const saveTo = text(source.save_to || defaults.save_to);
  const overrides = ENGINE_SLOTS.filter(k => source[k] !== undefined).length
    + Number(strings(filters.must_have).length > 0) + Number(strings(filters.block_keywords).length > 0)
    + Number(Boolean(text(source.preferences)));
  const setFilter = (key: string, list: string[]) => {
    const next = prune({ ...filters, [key]: list.length ? list : undefined });
    onChange({ filters: Object.keys(next).length ? next : undefined });
  };
  const providerOptions = (slot: string) => withCurrent(
    (catalog?.providers[slot] ?? []).map(x => ({ value: x.name, label: providerLabel(slot, x.name, catalog) })),
    text(source[slot]),
  );

  return (
    <Panel
      icon={Radio}
      title={name || <span className="text-muted-foreground">未命名来源</span>}
      description={`${approvalShortLabels[mode] ?? '确认方式未设置'} · 保存到「${saveTo || '未设置'}」`}
      actions={(
        <Button
          variant="ghost" size="icon" className="size-8 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          aria-label={`删除监听来源${name || index + 1}`} title="删除来源" onClick={onRemove}
        >
          <Trash2 />
        </Button>
      )}
      bodyClassName="p-4 sm:p-5"
    >
      {insight?.suggest_auto && mode === 'manual' ? (
        <Notice tone="success" icon={TrendingUp} title="可以考虑改为自动转存" className="mb-4"
          action={<Button size="sm" variant="outline" className="bg-card" onClick={() => onChange({ approval_mode: 'auto' })}>改为自动转存</Button>}>
          {insight.reason}。切换后仍要通过过滤规则、置信度、TMDB 匹配与分享清单校验，拿不准的照样交给你。
        </Notice>
      ) : null}
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Bot 或频道">
          <Input
            id={`source-${index}`} className="bg-card" placeholder="@resource_bot"
            value={text(source.bot)} onChange={e => onChange({ bot: e.target.value })}
          />
        </Field>
        <Field label="确认方式">
          <Select
            value={text(source.approval_mode)}
            onValueChange={v => onChange({ approval_mode: v || undefined })}
            placeholder="未设置" fallback={{ value: text(defaults.approval_mode) }}
            options={APPROVAL_OPTIONS.map(v => ({ value: v, label: approvalLabels[v] }))}
          />
        </Field>
        <Field label="保存到">
          <Select
            value={text(source.save_to)}
            onValueChange={v => onChange({ save_to: v || undefined })}
            placeholder="未设置" fallback={{ value: text(defaults.save_to) }}
            options={withCurrent(aliases.map(a => ({ value: a, label: a })), text(source.save_to))}
          />
        </Field>
      </div>

      {insight?.decisions ? (
        <p className="mt-3 text-xs text-muted-foreground">
          最近 {insight.decisions} 次确认：批准 {percent(insight.approve_rate)}，修正识别 {insight.corrected} 次
          {insight.advised ? `，按建议处理 ${insight.followed}/${insight.advised}` : ''}
        </p>
      ) : null}
      <div className="mt-4 border-t border-border/70 pt-3">
        <button
          type="button" onClick={onToggle} aria-expanded={open}
          className="flex min-h-9 w-full items-center gap-2 rounded-md text-left text-[13px] font-medium transition-colors hover:text-primary"
        >
          <SlidersHorizontal className="size-4 shrink-0 text-primary" />
          <span className="shrink-0">高级设置</span>
          <span className="min-w-0 truncate text-xs font-normal text-muted-foreground">
            {overrides ? `已单独设置 ${overrides} 项` : '识别方式、关键词过滤、收藏偏好'}
          </span>
          <ChevronDown className={cn('ml-auto size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')} />
        </button>
        {open ? (
          <div className="mt-3 space-y-4">
            <div className="grid gap-4 md:grid-cols-3">
              {ENGINE_SLOTS.map(slot => (
                <Field key={slot} label={SLOT_LABELS[slot]}>
                  <Select
                    value={text(source[slot])}
                    onValueChange={v => onChange({ [slot]: v || undefined })}
                    placeholder="未设置" fallback={{ value: text(defaults[slot]) }}
                    options={providerOptions(slot)}
                  />
                </Field>
              ))}
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="消息必须包含" hint="每行一个词；消息或链接里至少包含其中一个才会处理。">
                <LinesField value={strings(filters.must_have)} onChange={list => setFilter('must_have', list)} placeholder="如：115" />
              </Field>
              <Field label="包含这些词直接忽略" hint="每行一个词；命中任意一个就跳过。">
                <LinesField value={strings(filters.block_keywords)} onChange={list => setFilter('block_keywords', list)} placeholder="如：广告" />
              </Field>
            </div>
            <Field label="收藏偏好" hint="留空则沿用默认偏好；只在转存判断方式为「AI 按偏好判断」时生效。">
              <Textarea
                className="min-h-20 bg-card"
                value={text(source.preferences)}
                onChange={e => onChange({ preferences: e.target.value || undefined })}
                placeholder={text(defaults.preferences) || '如：只要 4K 或 1080p 的国产剧'}
              />
            </Field>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

interface SourcesTabProps {
  config: Obj;
  catalog: Catalog | null;
  update: (key: string, value: unknown) => void;
}

export function SourcesTab({ config, catalog, update }: SourcesTabProps) {
  const sources = rows(config.sources);
  const defaults = obj(config.defaults);
  const aliases = Object.keys(obj(config.save_dirs));
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [wizard, setWizard] = useState(false);
  const [insights, setInsights] = useState<Record<string, ISourceInsight>>({});
  useEffect(() => {
    let active = true;
    ai.insights()
      .then((r) => { if (active) setInsights(Object.fromEntries(r.sources.map((x) => [x.source.toLowerCase(), x]))); })
      .catch(() => undefined);  // 只影响提示
    return () => { active = false; };
  }, []);
  const addDraft = (draft: Obj) => {
    const name = text(draft.bot).trim().toLowerCase();
    const kept = sources.filter((s) => text(s.bot).trim().toLowerCase() !== name);
    update('sources', [...kept, draft]);
  };
  const wizardDialog = <SourceWizard open={wizard} onOpenChange={setWizard} onAdd={addDraft} />;

  const add = () => {
    const index = sources.length;
    update('sources', [...sources, { bot: '', save_to: defaults.save_to, approval_mode: 'manual' }]);
    requestAnimationFrame(() => {
      const input = document.getElementById(`source-${index}`);
      input?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      input?.focus({ preventScroll: true });
    });
  };
  const remove = (index: number) => {
    update('sources', sources.filter((_, n) => n !== index));
    setOpen(prev => new Set([...prev].filter(n => n !== index).map(n => (n > index ? n - 1 : n))));
  };
  const toggle = (index: number) => setOpen(prev => {
    const next = new Set(prev);
    if (next.has(index)) next.delete(index); else next.add(index);
    return next;
  });

  if (!sources.length) {
    return (
      <EmptyState
        icon={Radio}
        title="还没有监听来源"
        description="添加要盯的 Telegram 资源 Bot 或频道，Reelay 只会处理来自这些来源的消息。"
        action={(
          <div className="flex flex-wrap justify-center gap-2">
            <Button variant="outline" onClick={() => setWizard(true)}><Sparkles />用样例消息接入</Button>
            <Button onClick={add}><Plus />添加来源</Button>
            {wizardDialog}
          </div>
        )}
        className="bg-card"
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-[13px] leading-6 text-muted-foreground">
          共 <b className="font-semibold tabular-nums text-foreground">{sources.length}</b> 个来源。每个来源可以单独设置确认方式与保存位置，未设置的项沿用「基础」中的默认规则。
        </p>
        <div className="flex shrink-0 gap-2 self-start sm:self-auto">
          <Button variant="outline" onClick={() => setWizard(true)} title="贴几条样例消息，AI 起草这个来源的规则并试跑">
            <Sparkles />用样例消息接入
          </Button>
          <Button onClick={add}><Plus />添加来源</Button>
        </div>
      </div>
      {wizardDialog}
      {sources.map((source, i) => (
        <SourceCard
          key={i}
          index={i}
          source={source}
          defaults={defaults}
          aliases={aliases}
          catalog={catalog}
          open={open.has(i)}
          insight={insights[text(source.bot).trim().toLowerCase()]}
          onToggle={() => toggle(i)}
          onChange={patch => update('sources', sources.map((s, n) => (n === i ? prune({ ...s, ...patch }) : s)))}
          onRemove={() => remove(i)}
        />
      ))}
    </div>
  );
}
