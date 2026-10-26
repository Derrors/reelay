import { useState } from 'react';
import { ChevronDown, Plus, Radio, Regex, ScanSearch, SlidersHorizontal, Sparkles, Trash2, TrendingUp } from 'lucide-react';
import { sourceApi, type ISourceInsight } from '@/api/sources';
import { EmptyState } from '@/components/app/EmptyState';
import { Field } from '@/components/app/Field';
import { Notice } from '@/components/app/Notice';
import { Panel } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAsync } from '@/hooks/useAsync';
import { percent } from '@/lib/format';
import { approvalLabels, PARSE_RULE_FIELDS, PARSE_RULE_LABELS, SLOT_LABELS } from '@/lib/status';
import { cn } from '@/lib/utils';
import { obj, prune, rows, strings, text, type Obj } from '@/lib/values';
import { LinesField } from './fields';
import { ParseRulesDialog } from './ParseRulesDialog';
import { ParseTestDialog } from './ParseTestDialog';
import { SourceWizard } from './SourceWizard';
import { APPROVAL_OPTIONS, NOTIFY_OPTIONS, notifyValue, providerHint, providerLabel, type Catalog } from './shared';

/** 识别方式与放行方式只在来源上设置（defaults 里的值只是没写时的兜底）；网盘有多个可选时才出现在高级设置里。 */
const MAIN_SLOTS = ['parser', 'decision'];
const ADVANCED_SLOTS = ['storage'];
const multiple = (catalog: Catalog | null, slot: string) => (catalog?.providers[slot]?.length ?? 0) > 1;

interface SourceCardProps {
  index: number;
  source: Obj;
  defaults: Obj;
  aliases: string[];
  catalog: Catalog | null;
  open: boolean;
  /** 人工裁决统计与「改为 AI 处理」建议。 */
  insight?: ISourceInsight;
  onToggle: () => void;
  onChange: (patch: Obj) => void;
  onRemove: () => void;
}

function SourceCard({ index, source, defaults, aliases, catalog, open, insight, onToggle, onChange, onRemove }: SourceCardProps) {
  const name = text(source.bot).trim();
  const filters = obj(source.filters);
  const mode = text(source.approval_mode || defaults.approval_mode);
  const notify = (source.bot_notify ?? defaults.bot_notify) !== false;
  const saveTo = text(source.save_to || defaults.save_to);
  const parser = text(source.parser || defaults.parser);
  const parseRules = obj(source.parse_rules);
  const ruleFields = PARSE_RULE_FIELDS.filter(f => text(parseRules[f]).trim());
  const [rulesOpen, setRulesOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const advancedSlots = ADVANCED_SLOTS.filter(k => multiple(catalog, k) || source[k] !== undefined);
  const overrides = advancedSlots.filter(k => source[k] !== undefined).length
    + Number(strings(filters.must_have).length > 0) + Number(strings(filters.block_keywords).length > 0)
    + Number(Boolean(text(source.preferences)));
  const setFilter = (key: string, list: string[]) => {
    const next = prune({ ...filters, [key]: list.length ? list : undefined });
    onChange({ filters: Object.keys(next).length ? next : undefined });
  };
  // 当前值不在可选项里（扩展被卸载、目录被删）时，Select 会单独标出来，不会悄悄显示成别的值
  const providerOptions = (slot: string) =>
    (catalog?.providers[slot] ?? []).map(x => ({ value: x.name, label: providerLabel(slot, x.name, catalog) }));

  return (
    <Panel
      icon={Radio}
      title={name || <span className="text-muted-foreground">未命名来源</span>}
      description={`${approvalLabels[mode] ?? '确认方式未设置'} · ${notify ? 'Bot 通知' : '不发 Bot 通知'} · 保存到「${saveTo || '未设置'}」`}
      actions={(
        <Button
          variant="ghost" size="icon" className="size-9 text-muted-foreground hover:bg-destructive/10 hover:text-destructive sm:size-8"
          aria-label={`删除监听来源${name || index + 1}`} title="删除来源" onClick={onRemove}
        >
          <Trash2 />
        </Button>
      )}
      bodyClassName="p-4 sm:p-5"
    >
      {insight?.suggest_auto && mode === 'manual' ? (
        <Notice tone="success" icon={TrendingUp} title="可以考虑改为 AI 处理" className="mb-4"
          action={<Button size="sm" variant="outline" className="bg-card" onClick={() => onChange({ approval_mode: 'auto' })}>改为 AI 处理</Button>}>
          {insight.reason}。切换后仍要通过过滤规则、置信度、TMDB 匹配与分享清单校验，拿不准的照样交给你。
        </Notice>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
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
        {MAIN_SLOTS.map(slot => (
          <Field key={slot} label={SLOT_LABELS[slot]} hint={providerHint(slot, text(source[slot] || defaults[slot]), catalog) || undefined}>
            <Select
              value={text(source[slot] || defaults[slot])}
              onValueChange={v => onChange({ [slot]: v || undefined })}
              options={providerOptions(slot)}
            />
          </Field>
        ))}
        <Field label="Bot 通知">
          <Select
            value={notifyValue(source.bot_notify)}
            onValueChange={v => onChange({ bot_notify: v ? v === 'on' : undefined })}
            placeholder="未设置" fallback={{ value: defaults.bot_notify === false ? 'off' : 'on' }}
            options={NOTIFY_OPTIONS}
          />
        </Field>
        <Field label="保存到">
          <Select
            value={text(source.save_to)}
            onValueChange={v => onChange({ save_to: v || undefined })}
            placeholder="未设置" fallback={{ value: text(defaults.save_to) }}
            options={aliases.map(a => ({ value: a, label: a }))}
          />
        </Field>
      </div>

      <div className="mt-4 flex flex-col gap-2 rounded-lg border border-border/70 bg-background/60 px-3.5 py-2.5 sm:flex-row sm:items-center">
        <Regex className="hidden size-4 shrink-0 text-primary sm:block" />
        <div className="min-w-0 flex-1 text-[13px] leading-5">
          <span className="font-medium">识别规则</span>
          <span className="text-muted-foreground">
            {ruleFields.length
              ? `：已为${ruleFields.map(f => PARSE_RULE_LABELS[f]).join('、')}单独写了规则`
              : '：未设置，用通用规则'}
            {parser === 'regex' ? '' : '（AI 识别时作参考，AI 不可用时兜底）'}
          </span>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button size="sm" variant="outline" className="bg-card" onClick={() => setRulesOpen(true)}>
            {ruleFields.length ? '查看与修改' : '设置识别规则'}
          </Button>
          <Button size="sm" variant="outline" className="bg-card" onClick={() => setTesting(true)}
            title="按这个来源当前的设置（含未保存的修改）完整识别一条消息">
            <ScanSearch />测试识别
          </Button>
        </div>
      </div>
      {testing ? <ParseTestDialog open onOpenChange={setTesting} source={source} /> : null}
      {rulesOpen ? (
        <ParseRulesDialog
          open onOpenChange={setRulesOpen} bot={name} parser={parser} rules={parseRules}
          onSave={rules => onChange({ parse_rules: rules })}
        />
      ) : null}

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
            {overrides ? `已单独设置 ${overrides} 项` : `${advancedSlots.length ? '网盘、' : ''}关键词过滤、收藏偏好`}
          </span>
          <ChevronDown className={cn('ml-auto size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')} />
        </button>
        {open ? (
          <div className="mt-3 space-y-4">
            {advancedSlots.length ? (
            <div className="grid gap-4 md:grid-cols-3">
              {advancedSlots.map(slot => (
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
            ) : null}
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="消息必须包含" hint="每行一个词；消息或链接里至少包含其中一个才会处理。">
                <LinesField value={strings(filters.must_have)} onChange={list => setFilter('must_have', list)} placeholder="如：115" />
              </Field>
              <Field label="包含这些词直接忽略" hint="每行一个词；命中任意一个就跳过。">
                <LinesField value={strings(filters.block_keywords)} onChange={list => setFilter('block_keywords', list)} placeholder="如：广告" />
              </Field>
            </div>
            <Field label="收藏偏好" hint="留空则沿用默认偏好；只在放行方式为「AI 按偏好判断」时生效。">
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
  // 人工裁决统计只影响提示，读取失败时不显示
  const loaded = useAsync(() => sourceApi.insights(), []);
  const insights: Record<string, ISourceInsight> = Object.fromEntries((loaded.data?.sources ?? []).map((x) => [x.source.toLowerCase(), x]));
  // 新来源直接写明识别方式与放行方式（取推荐值），卡片上看到的就是它实际用的
  const engines = { parser: text(defaults.parser) || 'llm', decision: text(defaults.decision) || 'subscription' };
  const addDraft = (draft: Obj) => {
    const name = text(draft.bot).trim().toLowerCase();
    const kept = sources.filter((s) => text(s.bot).trim().toLowerCase() !== name);
    update('sources', [...kept, { ...engines, ...draft }]);
  };
  const wizardDialog = <SourceWizard open={wizard} onOpenChange={setWizard} onAdd={addDraft} />;

  const add = () => {
    const index = sources.length;
    update('sources', [...sources, { bot: '', save_to: defaults.save_to, approval_mode: 'manual', ...engines }]);
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
