import { useEffect, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Plus, Save, Trash2, Puzzle, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import PageHeader from '@/components/PageHeader';
import Auth115Card from '@/components/Auth115Card';
import PluginConfigEditor from '@/components/PluginConfigEditor';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import type { IConfigResponse, IExtension, IExtensionField } from '@/lib/types';
import { connectionLabels } from '@/lib/status';

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : {};
const rows = (v: unknown): Obj[] => Array.isArray(v) ? v as Obj[] : [];
const strings = (v: unknown): string[] => Array.isArray(v) ? v.map(String) : [];
const text = (v: unknown) => v === undefined || v === null ? '' : String(v);
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const slots: Record<string, string> = { source: '消息接入', parser: '资源解析', decision: '收藏决策', storage: '网盘存储', notify: '通知渠道' };
const stages: Record<string, string> = { after_parse: '识别之后', before_save: '转存之前', after_save: '转存之后' };
function Block({ title, description, children, action }: { title: string; description?: string; children: ReactNode; action?: ReactNode }) {
  return <Card className="rounded-lg shadow-none"><CardHeader className="flex-row flex-wrap items-start justify-between gap-3"><div><CardTitle className="text-base">{title}</CardTitle>{description && <CardDescription className="mt-2">{description}</CardDescription>}</div>{action}</CardHeader><CardContent className="space-y-4">{children}</CardContent></Card>;
}
function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="block min-w-0 space-y-1.5 text-sm"><span className="font-medium">{label}</span>{children}{hint && <span className="block text-xs text-muted-foreground">{hint}</span>}</label>;
}
function Select({ value, options, onChange, label }: { value: string; options: string[]; onChange: (value: string) => void; label: string }) {
  return <select aria-label={label} className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={value} onChange={e => onChange(e.target.value)}>
    {!options.includes(value) && <option value={value}>{value || '请选择'}</option>}{options.map(x => <option key={x} value={x}>{x}</option>)}
  </select>;
}
const getPath = (root: Obj, path: string) => path.split('.').reduce<unknown>((v, p) => obj(v)[p], root);
function setPath(root: Obj, path: string, value: unknown): Obj {
  const [first, ...rest] = path.split('.');
  return { ...root, [first]: rest.length ? setPath(obj(root[first]), rest.join('.'), value) : value };
}
function problems(cfg: Obj, extensions: IConfigResponse['extensions'] | undefined): string[] {
  const out: string[] = []; const dirs = obj(cfg.save_dirs); const d = obj(cfg.defaults);
  if (!Object.keys(dirs).length) out.push('目录映射：至少保留一个目录');
  Object.entries(dirs).forEach(([name, cid]) => { if (!name.trim() || name !== name.trim() || !/^[0-9]+$/.test(text(cid))) out.push(`目录映射「${name}」：别名不能为空或带首尾空格，CID必须为非负整数`); });
  const checkRule = (r: Obj, label: string) => {
    if (!Object.hasOwn(dirs, text(r.save_to))) out.push(`${label}：目标目录不存在`);
    if (!['manual', 'auto', 'default_pass'].includes(text(r.approval_mode))) out.push(`${label}：审批模式无效`);
    const n = r.confidence_threshold;
    if (typeof n !== 'number' || !Number.isFinite(n) || n < 0 || n > 1) out.push(`${label}：置信度必须在0到1之间`);
    if (text(r.preferences).length > 10000) out.push(`${label}：收藏偏好超过10000字`);
  };
  checkRule(d, '默认策略');
  const names = new Set<string>();
  rows(cfg.sources).forEach((s, i) => {
    const name = text(s.bot).trim().toLowerCase();
    if (!name || names.has(name)) out.push(`来源${i + 1}：名称不能为空或重复`);
    names.add(name); checkRule({ ...d, ...s }, `来源${i + 1}`);
  });
  const forward = obj(cfg.forward);
  if (forward.enabled && (!text(forward.target).trim() || !text(forward.template).trim())) out.push('转发：启用时目标与模板必填');
  const h = obj(cfg.approval).timeout_hours;
  if (typeof h !== 'number' || !Number.isFinite(h) || h <= 0) out.push('审批：超时小时数必须大于零');
  rows(cfg.routing).forEach((r, i) => {
    if (!Object.hasOwn(dirs, text(r.default ?? r.dir))) out.push(`路由${i + 1}：目录不存在`);
    if (!Object.hasOwn(r, 'default') && !text(r.when).trim()) out.push(`路由${i + 1}：条件不能为空`);
  });
  for (const x of [...(extensions?.hooks ?? []), ...(extensions?.events ?? [])]) for (const f of x.fields) {
    const v = getPath(cfg, f.path);
    if (v === undefined && !f.required) continue;
    if (f.type === 'number' && (typeof v !== 'number' || !Number.isFinite(v) || v < (f.min ?? -Infinity) || v > (f.max ?? Infinity))) out.push(`${x.label}：${f.label}超出允许范围`);
  }
  return [...new Set(out)];
}

export default function ConfigPage({ initialTab = 'common' }: { initialTab?: string }) {
  const [search] = useSearchParams();
  const [tab, setTab] = useState(search.get('tab') || initialTab);
  const [data, setData] = useState<IConfigResponse | null>(null);
  const [config, setConfig] = useState<Obj>({});
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  const [newAlias, setNewAlias] = useState('');
  const [newCid, setNewCid] = useState('');
  const load = async () => {
    setLoadError('');
    try { const r = await api.config(); setData(r); setConfig(r.config); setErrors([]); }
    catch (e) { setLoadError(e instanceof Error ? e.message : '配置加载失败'); }
  };
  useEffect(() => {
    let active = true;
    api.config().then(r => { if (active) { setData(r); setConfig(r.config); } })
      .catch(e => { if (active) setLoadError(e instanceof Error ? e.message : '配置加载失败'); });
    return () => { active = false; };
  }, []);
  const section = (key: string, value: unknown) => setConfig(c => ({ ...c, [key]: value }));
  const field = (key: string, name: string, value: unknown) => setConfig(c => ({ ...c, [key]: { ...obj(c[key]), [name]: value } }));
  const save = async () => {
    if (!data) return;
    const found = problems(config, data.extensions); setErrors(found);
    if (found.length) { toast.error('请先修正配置校验错误'); return; }
    const changed = Object.fromEntries(Object.entries(config).filter(([k, v]) => !equal(v, data.config[k])));
    if (!Object.keys(changed).length) { toast.info('没有待保存的更改'); return; }
    setBusy(true);
    try {
      await api.validateConfig(config);
      const r = await api.saveSections(changed, data.revision);
      setData({ ...data, config: r.config, revision: r.revision }); setConfig(r.config); setErrors([]);
      toast.success('配置块已校验并保存，新任务使用新配置');
    } catch (e) { const msg = e instanceof Error ? e.message : '保存失败'; setErrors([msg]); toast.error(msg); }
    finally { setBusy(false); }
  };
  const dirs = obj(config.save_dirs); const aliases = Object.keys(dirs);
  const defaults = obj(config.defaults); const sources = rows(config.sources);
  const forward = obj(config.forward); const approval = obj(config.approval);
  const routing = rows(config.routing);
  const providers = obj(config.providers); const toggles = obj(config.plugins);
  const catalog = data?.extensions;
  const providerNames = (slot: string) => (catalog?.providers[slot] ?? []).map(x => x.name);
  const updateSource = (i: number, patch: Obj) => section('sources', sources.map((s, n) => n === i ? { ...s, ...patch } : s));
  const addDir = () => {
    const alias = newAlias.trim(); const cid = newCid.trim();
    if (!alias || Object.hasOwn(dirs, alias) || !/^[0-9]+$/.test(cid)) { setErrors(['新增目录：别名必须非空且不重复；CID必须是十进制非负整数']); return; }
    section('save_dirs', { ...dirs, [alias]: cid }); setNewAlias(''); setNewCid(''); setErrors([]);
  };
  const removeDir = (alias: string) => {
    if (text(defaults.save_to) === alias || sources.some(s => s.save_to === alias) || routing.some(r => r.dir === alias || r.default === alias)) { setErrors([`目录「${alias}」仍被默认策略、来源或路由引用，请先修改引用`]); return; }
    const next = { ...dirs }; delete next[alias]; section('save_dirs', next);
  };
  const schemaField = (f: IExtensionField) => {
    const value = getPath(config, f.path);
    return <Field key={f.path} label={f.label} hint={f.type === 'number' ? `范围 ${f.min ?? '不限'} — ${f.max ?? '不限'}` : f.path}>
      {f.type === 'boolean' ? <Switch checked={Boolean(value)} onCheckedChange={v => setConfig(c => setPath(c, f.path, v))} />
        : f.choices ? <Select label={f.label} value={text(value)} options={f.choices} onChange={v => setConfig(c => setPath(c, f.path, v))} />
          : <Input value={text(value)} type={f.type === 'number' ? 'number' : 'text'} min={f.min} max={f.max} onChange={e => setConfig(c => setPath(c, f.path, f.type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value))} />}
    </Field>;
  };
  const extensionCard = (x: IExtension) => <Block key={`${x.kind}/${x.slot}/${x.name}`} title={x.label} description={x.description}
    action={<Switch aria-label={`启用${x.label}`} checked={toggles[x.name] !== false} onCheckedChange={v => field('plugins', x.name, v)} />}>
    <p className="text-xs text-muted-foreground">{x.kind === 'hook' ? 'Hook 钩子' : 'Event 事件'} · {stages[x.slot] ?? x.slot} · {x.name}{x.order !== null ? ` · 顺序 ${x.order}` : ''}</p>
    {x.required_storage && <p className="text-xs text-amber-700">依赖存储能力：{x.required_storage}；不兼容组合会在保存时拒绝。</p>}
    {x.fields.length > 0 && <div className="grid gap-4 sm:grid-cols-2">{x.fields.map(schemaField)}</div>}
    {x.config_type && <PluginConfigEditor extension={x} config={config} setConfig={setConfig} />}
    <p className="text-xs text-muted-foreground">{toggles[x.name] === false ? '停用后新任务不执行该扩展，已有任务快照保持不变。' : '开启后按阶段挂载；保存后生效。'}</p>
  </Block>;
  const pending = data && !equal(config, data.config);

  return <div className="mx-auto w-full max-w-7xl space-y-5 p-5 sm:p-8">
    <PageHeader eyebrow="配置工作台" title={initialTab === 'extensions' ? '插件与扩展管理' : '规则、目录与扩展配置'} description="主流程保持稳定；能力通过 Provider 选择，阶段处理通过 Hook 启停，旁路响应通过 Event 挂载。配置采用表单分块维护，无需编写配置文件。" />
    {loadError && <div role="alert" className="rounded border border-destructive/30 p-4 text-destructive">{loadError}<Button className="ml-3" variant="outline" onClick={() => void load()}>重试加载</Button></div>}
    {!data && !loadError && <p role="status">正在加载配置与扩展目录…</p>}
    {data && <>
      {data.config_error && <p role="alert" className="rounded border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{data.config_error}。原始文件存在错误，当前显示最后一份有效配置，保存已停用以防覆盖。</p>}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4 text-sm">
        <span>{pending ? '有未保存的配置更改' : '配置已同步'} · 版本 <code>{data.revision}</code></span>
        <div className="flex gap-2"><Button variant="outline" disabled={busy} onClick={() => { if (!pending || window.confirm('重新加载会放弃未保存的表单更改，继续？')) void load(); }}><RefreshCw className="size-4" />重新加载</Button><Button disabled={busy || Boolean(data.config_error)} onClick={() => void save()}><Save className="size-4" />{busy ? '正在校验保存' : '校验并保存更改'}</Button></div>
      </div>
      {errors.length > 0 && <div role="alert" className="rounded border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"><b>配置未保存，请修正以下问题</b><ul className="mt-2 list-inside list-disc">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
      <Tabs value={tab} onValueChange={setTab} className="gap-5"><TabsList className="flex h-auto w-fit flex-wrap"><TabsTrigger value="common">常用设置</TabsTrigger><TabsTrigger value="advanced">高级配置</TabsTrigger><TabsTrigger value="extensions">插件与扩展</TabsTrigger><TabsTrigger value="connections">连接状态</TabsTrigger></TabsList>
        <TabsContent value="common" className="space-y-5">
          <Block title="115 目录映射" description="新增映射只保存别名与CID，不会在网盘创建目录。被引用的别名不能删除；CID全程保留为字符串。">
            <div className="grid gap-3 sm:grid-cols-[1fr_2fr_auto]"><Input aria-label="新增目录别名" placeholder="目录别名，如 纪录片" value={newAlias} onChange={e => setNewAlias(e.target.value)} /><Input aria-label="新增目录CID" placeholder="115目录CID" value={newCid} onChange={e => setNewCid(e.target.value)} inputMode="numeric" /><Button variant="outline" onClick={addDir}><Plus className="size-4" />新增映射</Button></div>
            <div className="grid gap-4 md:grid-cols-2">{Object.entries(dirs).map(([alias, cid]) => <div key={alias} className="flex items-end gap-2"><div className="flex-1"><Field label={alias}><Input aria-label={`${alias}目录CID`} className="font-mono" value={text(cid)} onChange={e => field('save_dirs', alias, e.target.value)} /></Field></div><Button variant="ghost" size="icon" aria-label={`删除映射${alias}`} onClick={() => removeDir(alias)}><Trash2 className="size-4" /></Button></div>)}</div>
          </Block>
          <div className="grid gap-5 xl:grid-cols-2"><Block title="默认策略" description="来源未单独指定时继承默认值。">
            <div className="grid gap-4 sm:grid-cols-2"><Field label="审批模式"><Select label="默认审批模式" value={text(defaults.approval_mode)} options={['manual', 'auto', 'default_pass']} onChange={v => field('defaults', 'approval_mode', v)} /></Field><Field label="默认目录"><Select label="默认目录" value={text(defaults.save_to)} options={aliases} onChange={v => field('defaults', 'save_to', v)} /></Field>
            {['parser', 'decision', 'storage'].map(slot => <Field key={slot} label={slots[slot]}><Select label={`默认${slots[slot]}`} value={text(defaults[slot])} options={providerNames(slot)} onChange={v => field('defaults', slot, v)} /></Field>)}</div>
            <Field label="收藏偏好"><Textarea value={text(defaults.preferences)} onChange={e => field('defaults', 'preferences', e.target.value)} placeholder="如：只收藏4K电影" /></Field><p className="text-xs text-muted-foreground">manual 人工确认；auto 自动审批并通知；default_pass 静默通过。低置信度仍转人工。</p>
          </Block><Block title="识别后转发" description="资源识别后转发，独立于转存成功通知。"><div className="flex justify-between">启用转发<Switch checked={Boolean(forward.enabled)} onCheckedChange={v => field('forward', 'enabled', v)} /></div><Field label="目标Bot / 频道"><Input value={text(forward.target)} onChange={e => field('forward', 'target', e.target.value)} /></Field><Field label="消息模板" hint="可用字段：title、year、resolution、size、link"><Textarea className="min-h-28 font-mono text-xs" value={text(forward.template)} onChange={e => field('forward', 'template', e.target.value)} /></Field></Block></div>
          <Block title="监听来源" description="名称与目录等字段均独立编辑；保留来源中尚未展示的扩展字段。" action={<Button variant="outline" size="sm" onClick={() => section('sources', [...sources, { bot: '', save_to: defaults.save_to, approval_mode: 'manual' }])}><Plus className="size-4" />添加来源</Button>}>
            {!sources.length && <p className="text-muted-foreground">尚未配置来源</p>}
            {sources.map((s, i) => <div key={i} className="space-y-3 rounded-md border p-4"><div className="grid gap-3 md:grid-cols-[1.4fr_1fr_1fr_auto]"><Input aria-label={`来源${i + 1}名称`} value={text(s.bot)} placeholder="@resource_bot" onChange={e => updateSource(i, { bot: e.target.value })} /><Select label={`来源${i + 1}审批`} value={text(s.approval_mode ?? defaults.approval_mode)} options={['manual', 'auto', 'default_pass']} onChange={v => updateSource(i, { approval_mode: v })} /><Select label={`来源${i + 1}目录`} value={text(s.save_to ?? defaults.save_to)} options={aliases} onChange={v => updateSource(i, { save_to: v })} /><Button variant="ghost" size="icon" aria-label={`删除来源${i + 1}`} onClick={() => section('sources', sources.filter((_, n) => n !== i))}><Trash2 className="size-4" /></Button></div>
              <details><summary className="cursor-pointer text-sm text-primary">来源高级选项与过滤</summary><div className="mt-3 grid gap-3 md:grid-cols-3">{['parser', 'decision', 'storage'].map(slot => <Field key={slot} label={slots[slot]}><Select label={`来源${i + 1}${slot}`} value={text(s[slot] ?? defaults[slot])} options={providerNames(slot)} onChange={v => updateSource(i, { [slot]: v })} /></Field>)}{['must_have', 'block_keywords'].map(key => <Field key={key} label={key === 'must_have' ? '必须包含（每行一项）' : '屏蔽关键词（每行一项）'}><Textarea value={strings(obj(s.filters)[key]).join('\n')} onChange={e => updateSource(i, { filters: { ...obj(s.filters), [key]: e.target.value.split('\n') } })} /></Field>)}</div></details>
            </div>)}
          </Block>
        </TabsContent>
        <TabsContent value="advanced" className="space-y-5">
          <div className="grid gap-5 lg:grid-cols-2"><Block title="解析质量控制" description="不改变解析Provider，只调整可用的解析参数。"><Field label="最低置信度" hint="0—1，低于阈值转人工"><Input type="number" min="0" max="1" step="0.05" value={text(defaults.confidence_threshold)} onChange={e => field('defaults', 'confidence_threshold', e.target.value === '' ? '' : Number(e.target.value))} /></Field><Field label="LLM 输出格式"><Select label="LLM输出格式" value={text(defaults.llm_response_format)} options={['json_schema', 'json_object']} onChange={v => field('defaults', 'llm_response_format', v)} /></Field></Block>
          <Block title="审批超时处理" description="控制未处理审批的提醒或跳过策略。"><Field label="超时小时数"><Input type="number" min="0.01" step="0.5" value={text(approval.timeout_hours)} onChange={e => field('approval', 'timeout_hours', e.target.value === '' ? '' : Number(e.target.value))} /></Field><Field label="超时动作"><Select label="超时动作" value={text(approval.on_timeout)} options={['remind', 'skip']} onChange={v => field('approval', 'on_timeout', v)} /></Field></Block></div>
        </TabsContent>
        <TabsContent value="extensions" className="space-y-5">
          <div className="rounded-lg border bg-primary/5 p-5 text-sm"><b>稳定主流程 · 三类扩展契约</b><p className="mt-2 text-muted-foreground">监听 → 识别 → 转存由内核编排。以下目录从后端真实注册表读取，不展示尚未安装的虚构插件。新增插件代码后需重启服务完成发现；已安装扩展的配置和开关保存后用于新任务。</p></div>
          <Block title="Provider · 可替换能力槽" description="单选能力选择具体实现，通知渠道可以多选；来源可以覆盖默认解析/决策/存储。"><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(catalog?.providers ?? {}).filter(([slot]) => slot !== 'notify').map(([slot, entries]) => <Field key={slot} label={slots[slot] ?? slot} hint={entries.find(x => x.name === text(slot === 'source' ? providers.source : defaults[slot]))?.description}><Select label={`扩展${slot}`} value={text(slot === 'source' ? providers.source : defaults[slot])} options={entries.map(x => x.name)} onChange={v => field(slot === 'source' ? 'providers' : 'defaults', slot, v)} /></Field>)}</div><div className="space-y-3"><b className="text-sm">通知渠道</b>{(catalog?.providers.notify ?? []).map(x => <div key={x.name} className="flex items-center justify-between gap-3 rounded border p-3"><span className="text-sm">{x.label} · {x.description}</span><Switch aria-label={`通知渠道${x.name}`} checked={strings(providers.notify).includes(x.name)} onCheckedChange={v => field('providers', 'notify', v ? [...strings(providers.notify), x.name] : strings(providers.notify).filter(n => n !== x.name))} /></div>)}</div></Block>
          <h2 className="flex items-center gap-2 text-lg font-semibold"><Puzzle className="size-5" />Hook · 阶段钩子</h2><div className="grid gap-5 lg:grid-cols-2">{catalog?.hooks.map(extensionCard)}</div>
          <h2 className="text-lg font-semibold">Event · 旁路事件订阅</h2>{catalog?.events.length ? <div className="grid gap-5 lg:grid-cols-2">{catalog.events.map(extensionCard)}</div> : <div className="rounded border border-dashed p-6 text-sm text-muted-foreground">当前未注册 Event 订阅插件。安装并注册事件插件后会自动出现在这里；不会虚构可启用功能。</div>}
        </TabsContent>
        <TabsContent value="connections" className="space-y-5"><Auth115Card /><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{Object.entries(data.connections).filter(([key]) => key !== 'p115').map(([key, c]) => <Block key={key} title={c.label}><p className="text-sm">{connectionLabels[c.status] ?? c.status}</p><p className="text-xs text-muted-foreground">凭证不回显；已配置不等于连接验证成功。</p></Block>)}</div></TabsContent>
      </Tabs>
      <div className="flex justify-end border-t pt-5"><Button disabled={busy || Boolean(data.config_error)} onClick={() => void save()}><Save className="size-4" />校验并保存更改</Button></div>
    </>}
  </div>;
}
