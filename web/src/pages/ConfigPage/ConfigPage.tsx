import { useEffect, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ChevronDown, Clock3, FolderSearch, Info, Link2, Plus, Save, Settings2, Sparkles,
  Trash2, Puzzle, RefreshCw, X, FolderPlus, Send, Radio, ShieldCheck,
  SlidersHorizontal, Pencil,
} from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { extras } from '@/api/extras';
import PageHeader from '@/components/PageHeader';
import Auth115Card from '@/components/Auth115Card';
import PluginConfigEditor from '@/components/PluginConfigEditor';
import DirectoryPicker from '@/components/DirectoryPicker';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import type { IConfigResponse, IExtension, IExtensionField } from '@/lib/types';
import {
  approvalLabels, connectionLabels, llmFormatLabels, timeoutActionLabels,
} from '@/lib/status';

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : {};
const rows = (v: unknown): Obj[] => Array.isArray(v) ? v as Obj[] : [];
const strings = (v: unknown): string[] => Array.isArray(v) ? v.map(String) : [];
const text = (v: unknown) => v === undefined || v === null ? '' : String(v);
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
// 内部槽位 → 面向用户的中文名
const slots: Record<string, string> = { source: '消息接入方式', parser: '识别方式', decision: '转存判断方式', storage: '网盘', notify: '通知渠道' };
const stages: Record<string, string> = { after_parse: '识别完成之后', before_save: '转存之前', after_save: '转存完成之后' };
const approvalOptions = ['manual', 'auto', 'default_pass'];

function Block({ title, description, children, action, className = '', icon: Icon }: { title: string; description?: string; children: ReactNode; action?: ReactNode; className?: string; icon?: React.ComponentType<{ className?: string }> }) {
  return <Card className={`rounded-xl border-border/70 shadow-sm ${className}`}><CardHeader className="flex-row flex-wrap items-start justify-between gap-3 p-5 pb-0"><div className="flex items-start gap-2.5">{Icon ? <div className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-primary/8 text-primary"><Icon className="size-4" /></div> : null}<div><CardTitle className="text-[15px]">{title}</CardTitle>{description && <CardDescription className="mt-1.5 text-xs leading-5">{description}</CardDescription>}</div></div>{action}</CardHeader><CardContent className="space-y-4 p-5">{children}</CardContent></Card>;
}
function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return <label className="block min-w-0 space-y-1.5 text-[13px]"><span className="font-medium text-foreground/90">{label}</span>{children}{hint && <span className="block text-xs leading-5 text-muted-foreground">{hint}</span>}</label>;
}
function Select({ value, options, labels, onChange, label }: { value: string; options: string[]; labels?: Record<string, string>; onChange: (value: string) => void; label: string }) {
  return <select aria-label={label} className="h-10 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none transition focus:border-ring focus:ring-2 focus:ring-ring/20" value={value} onChange={e => onChange(e.target.value)}>
    {!options.includes(value) && <option value={value}>{labels?.[value] ?? value ?? '请选择'}</option>}{options.map(x => <option key={x} value={x}>{labels?.[x] ?? x}</option>)}
  </select>;
}
const getPath = (root: Obj, path: string) => path.split('.').reduce<unknown>((v, p) => obj(v)[p], root);
function setPath(root: Obj, path: string, value: unknown): Obj {
  const [first, ...rest] = path.split('.');
  return { ...root, [first]: rest.length ? setPath(obj(root[first]), rest.join('.'), value) : value };
}
function problems(cfg: Obj, extensions: IConfigResponse['extensions'] | undefined): string[] {
  const out: string[] = []; const dirs = obj(cfg.save_dirs); const d = obj(cfg.defaults);
  if (!Object.keys(dirs).length) out.push('请至少保留一个保存目录');
  Object.entries(dirs).forEach(([name, cid]) => { if (!name.trim() || name !== name.trim() || !/^[0-9]+$/.test(text(cid))) out.push(`目录「${name}」：名称不能为空或带首尾空格，目录编号须为纯数字`); });
  const checkRule = (r: Obj, label: string) => {
    if (!Object.hasOwn(dirs, text(r.save_to))) out.push(`${label}：选择的保存目录不存在，请重新选择`);
    if (!approvalOptions.includes(text(r.approval_mode))) out.push(`${label}：审批模式无效`);
    const n = r.confidence_threshold;
    if (typeof n !== 'number' || !Number.isFinite(n) || n < 0 || n > 1) out.push(`${label}：最低置信度必须在 0 到 1 之间`);
    if (text(r.preferences).length > 10000) out.push(`${label}：收藏偏好最多 10000 字`);
  };
  checkRule(d, '默认策略');
  const names = new Set<string>();
  rows(cfg.sources).forEach((s, i) => {
    const name = text(s.bot).trim().toLowerCase();
    if (!name || names.has(name)) out.push(`监听来源 ${i + 1}：名称不能为空或重复`);
    names.add(name); checkRule({ ...d, ...s }, `监听来源 ${i + 1}`);
  });
  const forward = obj(cfg.forward);
  if (forward.enabled && (!text(forward.target).trim() || !text(forward.template).trim())) out.push('消息转发：开启后需填写接收目标和消息模板');
  const h = obj(cfg.approval).timeout_hours;
  if (typeof h !== 'number' || !Number.isFinite(h) || h <= 0) out.push('审批超时：小时数必须大于 0');
  rows(cfg.routing).forEach((r, i) => {
    if (!Object.hasOwn(dirs, text(r.default ?? r.dir))) out.push(`分类规则 ${i + 1}：目标目录不存在`);
    if (!Object.hasOwn(r, 'default') && !text(r.when).trim()) out.push(`分类规则 ${i + 1}：条件不能为空`);
  });
  for (const x of [...(extensions?.hooks ?? []), ...(extensions?.events ?? [])]) for (const f of x.fields) {
    const v = getPath(cfg, f.path);
    if (v === undefined && !f.required) continue;
    if (f.type === 'number' && (typeof v !== 'number' || !Number.isFinite(v) || v < (f.min ?? -Infinity) || v > (f.max ?? Infinity))) out.push(`「${x.label}」：${f.label}超出允许范围`);
  }
  return [...new Set(out)];
}

export default function ConfigPage({ initialTab = 'common' }: { initialTab?: string }) {
  const [search] = useSearchParams();
  const extensionsOnly = initialTab === 'extensions';
  const requestedTab = search.get('tab');
  const [tab, setTab] = useState(extensionsOnly ? 'extensions' : (requestedTab && ['common', 'advanced', 'connections'].includes(requestedTab) ? requestedTab : 'common'));
  const [data, setData] = useState<IConfigResponse | null>(null);
  const [config, setConfig] = useState<Obj>({});
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  const [newAlias, setNewAlias] = useState('');
  const [newCid, setNewCid] = useState('');
  const [openPlugin, setOpenPlugin] = useState<string | null>(null);
  const [pluginSnapshots, setPluginSnapshots] = useState<Record<string, Obj>>({});
  const [picker, setPicker] = useState<{ alias?: string; cid: string; name: string } | null>(null);
  const [dirNames, setDirNames] = useState<Record<string, string>>({ '0': '根目录' });
  const resolveDirNames = async (cfg: Obj) => {
    const ids = [...new Set(Object.values(obj(cfg.save_dirs)).map(text))];
    const resolved: Record<string, string> = { '0': '根目录' };
    await Promise.all(ids.filter(id => id !== '0').map(async id => {
      try { const r = await extras.dirs(id); resolved[id] = r.path.at(-1)?.name || '目录名称暂不可用'; }
      catch { resolved[id] = '目录名称暂不可用'; }
    }));
    setDirNames(resolved);
  };
  const load = async () => {
    setLoadError('');
    try { const r = await api.config(); setData(r); setConfig(r.config); setErrors([]); void resolveDirNames(r.config); }
    catch (e) { setLoadError(e instanceof Error ? e.message : '配置加载失败'); }
  };
  useEffect(() => {
    let active = true;
    api.config().then(r => { if (active) { setData(r); setConfig(r.config); void resolveDirNames(r.config); } })
      .catch(e => { if (active) setLoadError(e instanceof Error ? e.message : '配置加载失败'); });
    return () => { active = false; };
  }, []);
  const section = (key: string, value: unknown) => setConfig(c => ({ ...c, [key]: value }));
  const field = (key: string, name: string, value: unknown) => setConfig(c => ({ ...c, [key]: { ...obj(c[key]), [name]: value } }));
  const persist = async (keys: string[], success: string): Promise<boolean> => {
    if (!data) return false;
    const found = problems(config, data.extensions); setErrors(found);
    if (found.length) { toast.error('请先修正下面标红的问题'); return false; }
    const changed = Object.fromEntries(keys.filter(k => !equal(config[k], data.config[k])).map(k => [k, config[k]]));
    if (!Object.keys(changed).length) { toast.info('没有需要保存的修改'); return true; }
    setBusy(true);
    try {
      await api.validateConfig(config);
      const r = await api.saveSections(changed, data.revision);
      setData({ ...data, config: r.config, revision: r.revision }); setConfig(r.config); setErrors([]);
      toast.success(success); return true;
    } catch (e) { const msg = e instanceof Error ? e.message : '保存失败'; setErrors([msg]); toast.error(msg); return false; }
    finally { setBusy(false); }
  };
  const restore = (keys: string[]) => {
    if (!data) return;
    setConfig(c => ({ ...c, ...Object.fromEntries(keys.map(k => [k, structuredClone(data.config[k])])) }));
    setErrors([]);
  };
  const systemKeys = ['save_dirs', 'defaults', 'sources', 'forward', 'approval'];
  const dirs = obj(config.save_dirs); const aliases = Object.keys(dirs);
  const defaults = obj(config.defaults); const sources = rows(config.sources);
  const forward = obj(config.forward); const approval = obj(config.approval);
  const routing = rows(config.routing);
  const providers = obj(config.providers); const toggles = obj(config.plugins);
  const catalog = data?.extensions;
  const providerNames = (slot: string) => (catalog?.providers[slot] ?? []).map(x => x.name);
  const extensionKeys = (x: IExtension) => [...new Set([
    ...(x.config_section ? [x.config_section] : []),
    ...x.fields.map(f => f.path.split('.')[0]),
  ])];
  const openExtension = (x: IExtension) => {
    if (openPlugin) return;
    const keys = extensionKeys(x);
    setPluginSnapshots(v => ({ ...v, [x.name]: Object.fromEntries(keys.map(k => [k, structuredClone(config[k])])) }));
    setOpenPlugin(x.name);
  };
  const cancelExtension = (x: IExtension) => {
    const snapshot = pluginSnapshots[x.name];
    if (snapshot) setConfig(c => ({ ...c, ...structuredClone(snapshot) }));
    setOpenPlugin(null); setErrors([]);
  };
  const saveExtension = async (x: IExtension) => {
    if (await persist(extensionKeys(x), `「${x.label}」设置已保存并立即生效`)) setOpenPlugin(null);
  };
  const toggleExtension = async (x: IExtension, enabled: boolean) => {
    if (!data || busy || openPlugin) return;
    const previous = config;
    const next = { ...config, plugins: { ...obj(config.plugins), [x.name]: enabled } };
    setConfig(next); setBusy(true);
    try {
      await api.validateConfig(next);
      const r = await api.saveSections({ plugins: next.plugins }, data.revision);
      setData({ ...data, config: r.config, revision: r.revision }); setConfig(r.config);
      toast.success(`「${x.label}」已${enabled ? '开启' : '关闭'}并立即生效`);
    } catch (e) { setConfig(previous); toast.error(e instanceof Error ? e.message : '设置保存失败'); }
    finally { setBusy(false); }
  };
  const updateSource = (i: number, patch: Obj) => section('sources', sources.map((s, n) => n === i ? { ...s, ...patch } : s));
  const addDir = () => {
    const alias = newAlias.trim(); const cid = newCid.trim();
    if (!alias || Object.hasOwn(dirs, alias) || !/^[0-9]+$/.test(cid)) { setErrors(['请填写不重复的目录名称，并从网盘中选择对应目录']); return; }
    section('save_dirs', { ...dirs, [alias]: cid }); setNewAlias(''); setNewCid(''); setErrors([]);
  };
  const removeDir = (alias: string) => {
    if (text(defaults.save_to) === alias || sources.some(s => s.save_to === alias) || routing.some(r => r.dir === alias || r.default === alias)) { setErrors([`目录「${alias}」正在被默认策略、监听来源或分类规则使用，请先改完再删除`]); return; }
    const next = { ...dirs }; delete next[alias]; section('save_dirs', next);
  };
  const schemaField = (f: IExtensionField) => {
    const value = getPath(config, f.path);
    return <Field key={f.path} label={f.label} hint={f.type === 'number' ? `允许范围：${f.min ?? '不限'} ~ ${f.max ?? '不限'}` : undefined}>
      {f.type === 'boolean' ? <Switch checked={Boolean(value)} onCheckedChange={v => setConfig(c => setPath(c, f.path, v))} />
        : f.choices ? <Select label={f.label} value={text(value)} options={f.choices} onChange={v => setConfig(c => setPath(c, f.path, v))} />
          : <Input value={text(value)} type={f.type === 'number' ? 'number' : 'text'} min={f.min} max={f.max} onChange={e => setConfig(c => setPath(c, f.path, f.type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value))} />}
    </Field>;
  };
  const systemDirty = Boolean(data && systemKeys.some(k => !equal(config[k], data.config[k]))) || Boolean(newAlias || newCid);
  const providerDirty = Boolean(data && ['providers', 'defaults'].some(k => !equal(config[k], data.config[k])));
  const activeExtension = [...(catalog?.hooks ?? []), ...(catalog?.events ?? [])].find(x => x.name === openPlugin);
  const activePluginDirty = Boolean(data && activeExtension && extensionKeys(activeExtension).some(k => !equal(config[k], data.config[k])));
  const extensionCard = (x: IExtension) => {
    const configurable = x.fields.length > 0 || Boolean(x.config_type);
    const opened = openPlugin === x.name;
    const dirty = Boolean(data && extensionKeys(x).some(k => !equal(config[k], data.config[k])));
    const enabled = toggles[x.name] !== false;
    const stageLabel = stages[x.slot];
    return (
      <Block
        key={`${x.kind}/${x.slot}/${x.name}`}
        className={opened ? 'lg:col-span-2' : ''}
        title={x.label}
        description={x.description}
        action={<Switch aria-label={`开关${x.label}`} checked={enabled} disabled={busy} onCheckedChange={v => void toggleExtension(x, v)} />}
      >
        {x.required_storage ? (
          <p className="flex items-start gap-1.5 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-700">
            <Info className="mt-px size-3.5 shrink-0" />
            该功能需要「{x.required_storage === 'p115' ? '115 网盘' : x.required_storage}」能力，不支持的网盘组合会在保存时提示。
          </p>
        ) : null}
        {configurable && opened ? (
          <div className="space-y-4 rounded-xl border bg-muted/25 p-4">
            {x.fields.length > 0 ? <div className="grid gap-4 sm:grid-cols-2">{x.fields.map(schemaField)}</div> : null}
            {x.config_type ? <PluginConfigEditor extension={x} config={config} setConfig={setConfig} /> : null}
          </div>
        ) : null}
        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-xs text-muted-foreground">
            {enabled ? '已开启，新任务会自动执行' : '已关闭，新任务不会执行该功能'}
            {stageLabel ? ` · 在「${stageLabel}」运行` : ''}
          </p>
          <div className="flex gap-2">
            {opened ? (
              <>
                <Button variant="outline" size="sm" disabled={busy} onClick={() => cancelExtension(x)}>
                  <X className="size-4" />{dirty ? '取消修改' : '收起'}
                </Button>
                {dirty ? (
                  <Button size="sm" disabled={busy} onClick={() => void saveExtension(x)}>
                    <Save className="size-4" />保存
                  </Button>
                ) : null}
              </>
            ) : configurable ? (
              <Button type="button" variant="outline" size="sm" disabled={Boolean(openPlugin)} onClick={() => openExtension(x)}>
                <Settings2 className="size-4" />设置<ChevronDown className="size-4" />
              </Button>
            ) : null}
          </div>
        </div>
      </Block>
    );
  };

  return <div className="mx-auto w-full max-w-7xl space-y-5 p-5 sm:p-8">
    <PageHeader eyebrow={extensionsOnly ? '插件管理' : '系统设置'} title={extensionsOnly ? '插件与扩展' : '系统设置'} description={extensionsOnly ? '开启或关闭已安装的功能插件，并按需调整它们的参数；不会展示尚未安装的功能。' : '配置保存目录、监听来源、确认方式和消息转发；115 连接与插件参数在各自页签中维护。'} />
    {loadError && <div role="alert" className="flex items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{loadError}<Button className="ml-auto" variant="outline" onClick={() => void load()}>重试加载</Button></div>}
    {!data && !loadError && <p role="status" className="text-sm text-muted-foreground">正在加载设置…</p>}
    {data && <>
      {data.config_error && <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{data.config_error}。当前配置文件存在错误，页面显示的是最后一份可用配置，保存已暂停以免覆盖。</p>}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/70 bg-card px-4 py-3 text-sm shadow-sm">
        <span className="flex items-center gap-2">
          <span className={`size-2 rounded-full ${systemDirty || providerDirty || activePluginDirty ? 'bg-warning' : 'bg-success'}`} />
          {systemDirty || providerDirty || activePluginDirty ? '有修改还没有保存' : '所有设置均已保存并生效'}
        </span>
        <div className="flex gap-2">{!extensionsOnly && systemDirty ? <><Button variant="outline" disabled={busy} onClick={() => { restore(systemKeys); setNewAlias(''); setNewCid(''); }}><X className="size-4" />放弃修改</Button><Button disabled={busy || Boolean(data.config_error)} onClick={() => void persist(systemKeys, '设置已保存并立即生效').then(ok => { if (ok) { setNewAlias(''); setNewCid(''); void resolveDirNames(config); } })}><Save className="size-4" />保存设置</Button></> : <Button variant="outline" disabled={busy} onClick={() => void load()}><RefreshCw className="size-4" />重新加载</Button>}</div>
      </div>
      {errors.length > 0 && <div role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"><b>设置还没有保存，请先处理以下问题</b><ul className="mt-2 list-inside list-disc space-y-1">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
      <Tabs value={tab} onValueChange={setTab} className="gap-5">
        {!extensionsOnly && <TabsList className="flex h-auto w-fit flex-wrap rounded-lg bg-muted/60 p-1"><TabsTrigger value="common" className="rounded-md">基础设置</TabsTrigger><TabsTrigger value="advanced" className="rounded-md">识别与确认</TabsTrigger><TabsTrigger value="connections" className="rounded-md">115 连接</TabsTrigger></TabsList>}

        {!extensionsOnly && <TabsContent value="common" className="space-y-5"><div className="space-y-5">
          <Block icon={FolderPlus} title="保存目录" description="给网盘里的常用目录取一个好记的名字，之后选择保存位置时直接选名字即可，无需面对一长串编号。">
            <div className="grid gap-3 sm:grid-cols-[1fr_2fr_auto]">
              <Input aria-label="新目录名称" placeholder="给目录取个名字，如：纪录片" value={newAlias} onChange={e => setNewAlias(e.target.value)} />
              <Button type="button" variant="outline" className="justify-start" onClick={() => setPicker({ cid: newCid || '0', name: newCid ? '已选目录' : '根目录' })}><FolderSearch className="size-4 text-primary" />{newCid ? (dirNames[newCid] || '已选择目录') : '从 115 网盘中选择目录'}</Button>
              <Button onClick={addDir}><Plus className="size-4" />添加</Button>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {Object.entries(dirs).map(([alias, cid]) => <div key={alias} className="flex items-center gap-2 rounded-lg border bg-muted/20 p-2.5 pl-3.5">
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium">{alias}</div>
                  <button type="button" onClick={() => setPicker({ alias, cid: text(cid), name: dirNames[text(cid)] || alias })} className="mt-0.5 flex max-w-full items-center gap-1 text-xs text-primary hover:underline">
                    <FolderSearch className="size-3 shrink-0" /><span className="truncate">{dirNames[text(cid)] || '目录名称暂不可用'}</span>
                  </button>
                </div>
                <Button variant="ghost" size="icon" aria-label={`删除目录${alias}`} onClick={() => removeDir(alias)}><Trash2 className="size-4" /></Button>
              </div>)}
            </div>
          </Block>

          <div className="grid gap-5 xl:grid-cols-2">
            <Block icon={ShieldCheck} title="默认确认方式" description="某个监听来源没有单独设置时，使用这里的默认规则。">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="新资源如何处理"><Select label="默认确认方式" value={text(defaults.approval_mode)} options={approvalOptions} labels={approvalLabels} onChange={v => field('defaults', 'approval_mode', v)} /></Field>
                <Field label="默认保存到"><Select label="默认保存目录" value={text(defaults.save_to)} options={aliases} onChange={v => field('defaults', 'save_to', v)} /></Field>
              </div>
              <Field label="收藏偏好（用大白话告诉 AI 你想要什么）" hint="例如：只存 4K 电影；综艺和广告一律不要；优先杜比视界版本。AI 会按这句话辅助判断。"><Textarea value={text(defaults.preferences)} onChange={e => field('defaults', 'preferences', e.target.value)} placeholder="如：只收藏 4K 电影，剧集优先内封中字版本" /></Field>
              <div className="rounded-lg bg-primary/[0.06] px-3 py-2.5 text-xs leading-5 text-primary/90">三种确认方式：<b>每条确认</b>最稳妥；<b>自动通过并通知</b>适合你信任的来源，存完仍可撤回；<b>静默通过</b>完全自动。即使选了自动，AI 没把握时仍会转人工确认。</div>
            </Block>

            <Block icon={Send} title="识别后转发" description="识别出资源后，另外把整理好的消息转发到指定 Bot 或频道；与转存是否成功无关，可随时关闭。">
              <div className="flex items-center justify-between rounded-lg border px-3.5 py-3">
                <div><div className="text-[13px] font-medium">开启转发</div><div className="text-xs text-muted-foreground">关闭后仅转存，不发送任何转发消息</div></div>
                <Switch checked={Boolean(forward.enabled)} onCheckedChange={v => field('forward', 'enabled', v)} />
              </div>
              <Field label="转发到哪里（Bot 或频道）"><Input value={text(forward.target)} onChange={e => field('forward', 'target', e.target.value)} placeholder="@我的归档频道" /></Field>
              <Field label="转发消息样式" hint="可用字段：片名 {title}、年份 {year}、清晰度 {resolution}、大小 {size}、链接 {link}">
                <Textarea className="min-h-28 text-xs leading-6" value={text(forward.template)} onChange={e => field('forward', 'template', e.target.value)} placeholder="🎬 {title} ({year}) {resolution}&#10;📦 {size}&#10;🔗 {link}" /></Field>
            </Block>
          </div>

          <Block icon={Radio} title="监听来源" description="填写要盯的 Telegram 资源 Bot，每个来源可以单独设置确认方式与保存目录。" action={<Button variant="outline" size="sm" onClick={() => section('sources', [...sources, { bot: '', save_to: defaults.save_to, approval_mode: 'manual' }])}><Plus className="size-4" />添加监听来源</Button>}>
            {!sources.length && <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">还没有监听来源，点右上角「添加监听来源」开始</p>}
            {sources.map((s, i) => <div key={i} className="space-y-3 rounded-xl border bg-card p-4">
              <div className="grid gap-3 md:grid-cols-[1.4fr_1.2fr_1fr_auto]">
                <Input aria-label={`监听来源${i + 1}`} value={text(s.bot)} placeholder="@resource_bot" onChange={e => updateSource(i, { bot: e.target.value })} />
                <Select label={`来源${i + 1}确认方式`} value={text(s.approval_mode ?? defaults.approval_mode)} options={approvalOptions} labels={approvalLabels} onChange={v => updateSource(i, { approval_mode: v })} />
                <Select label={`来源${i + 1}保存目录`} value={text(s.save_to ?? defaults.save_to)} options={aliases} onChange={v => updateSource(i, { save_to: v })} />
                <Button variant="ghost" size="icon" aria-label={`删除监听来源${i + 1}`} onClick={() => section('sources', sources.filter((_, n) => n !== i))}><Trash2 className="size-4" /></Button>
              </div>
              <details className="group">
                <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[13px] font-medium text-primary [&::-webkit-details-marker]:none"><SlidersHorizontal className="size-3.5" />高级：识别引擎与关键词过滤<ChevronDown className="size-3.5 transition group-open:rotate-180" /></summary>
                <div className="mt-3 grid gap-3 md:grid-cols-3">
                  {['parser', 'decision', 'storage'].map(slot => <Field key={slot} label={slots[slot]}><Select label={`来源${i + 1}${slot}`} value={text(s[slot] ?? defaults[slot])} options={providerNames(slot)} onChange={v => updateSource(i, { [slot]: v })} /></Field>)}
                  {['must_have', 'block_keywords'].map(key => <Field key={key} label={key === 'must_have' ? '消息必须包含（每行一个）' : '包含这些词直接忽略（每行一个）'}><Textarea value={strings(obj(s.filters)[key]).join('\n')} onChange={e => updateSource(i, { filters: { ...obj(s.filters), [key]: e.target.value.split('\n') } })} /></Field>)}
                </div>
              </details>
            </div>)}
          </Block>
        </div></TabsContent>}

        {!extensionsOnly && <TabsContent value="advanced" className="space-y-5"><div className="grid gap-5 lg:grid-cols-2">
          <Block icon={Sparkles} title="识别准确度" description="控制 AI 多有把握才自动处理；没把握的资源会转交给你确认。">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="最低置信度" hint="0~1 之间，越高越谨慎；建议 0.6 左右"><Input type="number" min="0" max="1" step="0.05" value={text(defaults.confidence_threshold)} onChange={e => field('defaults', 'confidence_threshold', e.target.value === '' ? '' : Number(e.target.value))} /></Field>
              <Field label="AI 返回格式" hint="老模型不支持结构化输出时再切换"><Select label="AI 返回格式" value={text(defaults.llm_response_format)} options={['json_schema', 'json_object']} labels={llmFormatLabels} onChange={v => field('defaults', 'llm_response_format', v)} /></Field>
            </div>
          </Block>
          <Block icon={Clock3} title="确认超时" description="一条资源等你确认太久时怎么处理。">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="等待多少小时算超时"><Input type="number" min="0.01" step="0.5" value={text(approval.timeout_hours)} onChange={e => field('approval', 'timeout_hours', e.target.value === '' ? '' : Number(e.target.value))} /></Field>
              <Field label="超时后怎么做"><Select label="超时动作" value={text(approval.on_timeout)} options={['remind', 'skip']} labels={timeoutActionLabels} onChange={v => field('approval', 'on_timeout', v)} /></Field>
            </div>
          </Block>
        </div></TabsContent>}

        {extensionsOnly && <TabsContent value="extensions" className="space-y-5">
          <div className="flex items-start gap-3 rounded-xl border border-primary/20 bg-primary/[0.05] p-4 text-sm">
            <Puzzle className="mt-0.5 size-5 shrink-0 text-primary" />
            <div><b>功能插件即开即用</b><p className="mt-1 leading-6 text-muted-foreground">「监听 → 识别 → 转存」主流程由系统稳定运行，下面列出的都是已安装、可开关的附加功能。安装新插件后重启服务即会自动出现；插件设置保存后只对新任务生效。</p></div>
          </div>
          <Block icon={Puzzle} title="核心能力选择" description="为识别、判断、网盘、通知等环节选择具体实现；通知渠道可以同时开启多个。">
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(catalog?.providers ?? {}).filter(([slot]) => slot !== 'notify').map(([slot, entries]) => <Field key={slot} label={slots[slot] ?? slot} hint={entries.find(x => x.name === text(slot === 'source' ? providers.source : defaults[slot]))?.description}><Select label={`能力${slot}`} value={text(slot === 'source' ? providers.source : defaults[slot])} options={entries.map(x => x.name)} onChange={v => field(slot === 'source' ? 'providers' : 'defaults', slot, v)} /></Field>)}</div>
              <div className="space-y-3">
                <b className="text-[13px]">通知渠道（可多选）</b>
                {(catalog?.providers.notify ?? []).map(x => <div key={x.name} className="flex items-center justify-between gap-3 rounded-lg border px-3.5 py-3"><div><div className="text-[13px] font-medium">{x.label}</div><div className="mt-0.5 text-xs text-muted-foreground">{x.description}</div></div><Switch aria-label={`通知渠道${x.name}`} checked={strings(providers.notify).includes(x.name)} onCheckedChange={v => field('providers', 'notify', v ? [...strings(providers.notify), x.name] : strings(providers.notify).filter(n => n !== x.name))} /></div>)}
              </div>
            </div>
            {providerDirty && <div className="flex justify-end gap-2 pt-1"><Button size="sm" variant="outline" disabled={busy} onClick={() => restore(['providers', 'defaults'])}><X className="size-4" />放弃修改</Button><Button size="sm" disabled={busy} onClick={() => void persist(['providers', 'defaults'], '能力选择已保存并立即生效')}><Save className="size-4" />保存</Button></div>}
          </Block>
          <h2 className="flex items-center gap-2 pt-1 text-base font-semibold"><Pencil className="size-4 text-primary" />自动处理动作（在固定环节执行）</h2>
          <div className="grid gap-5 lg:grid-cols-2">{catalog?.hooks.map(extensionCard)}</div>
          <h2 className="flex items-center gap-2 pt-1 text-base font-semibold"><Link2 className="size-4 text-primary" />事件通知（不影响主流程的后台动作）</h2>
          {catalog?.events.length ? <div className="grid gap-5 lg:grid-cols-2">{catalog.events.map(extensionCard)}</div> : <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">当前没有已安装的事件类插件。安装相关插件并重启服务后，会自动出现在这里。</div>}
        </TabsContent>}

        {!extensionsOnly && <TabsContent value="connections" className="space-y-5">
          <Auth115Card />
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{Object.entries(data.connections).filter(([key]) => key !== 'p115').map(([key, c]) => <Block key={key} title={c.label}><p className="flex items-center gap-1.5 text-sm">{c.status === 'connected' ? '🟢' : '⚪️'} {connectionLabels[c.status] ?? c.status}</p><p className="text-xs text-muted-foreground">已填写凭证不等于连接成功，以实际验证状态为准。</p></Block>)}</div>
        </TabsContent>}
      </Tabs>
    </>}
    {picker && <DirectoryPicker open initialCid={picker.cid} initialName={picker.name} onOpenChange={open => { if (!open) setPicker(null); }} onSelect={dir => { setDirNames(v => ({ ...v, [dir.id]: dir.name })); if (picker.alias) field('save_dirs', picker.alias, dir.id); else setNewCid(dir.id); }} />}
  </div>;
}
