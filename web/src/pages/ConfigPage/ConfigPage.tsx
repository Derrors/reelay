import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CircleAlert, Loader2, RefreshCw, RotateCcw, Save } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { extras } from '@/api/extras';
import { Page } from '@/components/app/Page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type { IConfigResponse, IExtension } from '@/lib/types';
import { BasicTab } from './BasicTab';
import { ConnectionsTab } from './ConnectionsTab';
import { ExtensionsTab } from './ExtensionsTab';
import { Notice } from './fields';
import { RecognitionTab } from './RecognitionTab';
import { SourcesTab } from './SourcesTab';
import {
  CONFIG_SECTIONS, SYSTEM_KEYS, equal, extensionKeys, getPath, mergeByName, obj, problems, sortHooks, text,
  type Catalog, type Obj,
} from './shared';

const TABS = [
  { value: 'common', label: '基础' },
  { value: 'sources', label: '监听来源' },
  { value: 'advanced', label: '识别与确认' },
  { value: 'connections', label: '115 连接' },
];
const CORE_KEYS = ['providers', 'defaults'];
const TRIGGER_CLASS = [
  'h-8 gap-1.5 rounded-md px-2 text-[13px] text-muted-foreground sm:px-3.5',
  'data-[state=active]:bg-card data-[state=active]:text-foreground data-[state=active]:shadow-xs',
  'dark:text-muted-foreground dark:data-[state=active]:border-transparent dark:data-[state=active]:bg-card dark:data-[state=active]:text-foreground',
].join(' ');

type DirNames = Record<string, string | null>;

/** 读取各保存目录的网盘名称；失败记为 null（界面显示「未能读取」）。 */
async function resolveDirNames(cfg: Obj): Promise<DirNames> {
  const ids = [...new Set(Object.values(obj(cfg.save_dirs)).map(text))].filter(id => id !== '0');
  const entries = await Promise.all(ids.map(async id => {
    try { return [id, (await extras.dirs(id)).path.at(-1)?.name || null] as const; }
    catch { return [id, null] as const; }
  }));
  return { '0': '根目录', ...Object.fromEntries(entries) };
}
const mergeNames = (prev: DirNames, next: DirNames): DirNames =>
  Object.fromEntries(Object.entries(next).map(([id, name]) => [id, name ?? prev[id] ?? null]));
const message = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

function LoadingState() {
  return (
    <div className="space-y-5" role="status" aria-label="正在加载设置">
      <Skeleton className="h-9 w-full rounded-lg sm:w-96" />
      <Skeleton className="h-56 rounded-xl" />
      <div className="grid gap-5 xl:grid-cols-2">
        <Skeleton className="h-80 rounded-xl" />
        <Skeleton className="h-80 rounded-xl" />
      </div>
    </div>
  );
}

export default function ConfigPage({ initialTab = 'common' }: { initialTab?: string }) {
  const extensionsOnly = initialTab === 'extensions';
  const [search, setSearch] = useSearchParams();
  const isTab = (value: string | null) => TABS.some(t => t.value === value);
  const requested = search.get('tab');
  const tab = isTab(requested) ? requested! : isTab(initialTab) ? initialTab : 'common';
  const setTab = (value: string) => setSearch(prev => {
    const next = new URLSearchParams(prev);
    next.set('tab', value);
    return next;
  }, { replace: true });

  const [data, setData] = useState<IConfigResponse | null>(null);
  const [config, setConfig] = useState<Obj>({});
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  const [dirNames, setDirNames] = useState<DirNames>({ '0': '根目录' });
  const errorRef = useRef<HTMLDivElement>(null);

  const apply = useCallback((r: IConfigResponse) => {
    setData(r); setConfig(r.config); setErrors([]); setLoadError('');
    void resolveDirNames(r.config).then(names => setDirNames(prev => mergeNames(prev, names)));
  }, []);
  useEffect(() => {
    let active = true;
    api.config()
      .then(r => { if (active) apply(r); })
      .catch(e => { if (active) setLoadError(message(e, '配置加载失败')); });
    return () => { active = false; };
  }, [apply]);
  const reload = async () => {
    setLoadError('');
    try { apply(await api.config()); } catch (e) { setLoadError(message(e, '配置加载失败')); }
  };

  const catalog = (data?.extensions ?? null) as Catalog | null;
  const hooks = sortHooks(mergeByName(catalog?.hooks ?? []));
  const events = mergeByName(catalog?.events ?? []);
  const extensionList = [...hooks, ...events];
  const scopeKeys = extensionsOnly ? [...new Set([...CORE_KEYS, ...extensionList.flatMap(extensionKeys)])] : SYSTEM_KEYS;
  const dirtyKeys = data ? scopeKeys.filter(k => !equal(config[k], data.config[k])) : [];
  const dirty = dirtyKeys.length > 0;
  const dirtySections = data && !extensionsOnly
    ? CONFIG_SECTIONS.filter(s => s.paths.some(p => !equal(getPath(config, p), getPath(data.config, p))))
    : [];
  const dirtyLabels = extensionsOnly
    ? [...new Set(dirtyKeys.flatMap(k => CORE_KEYS.includes(k) ? ['核心能力']
      : extensionList.filter(x => extensionKeys(x).includes(k)).map(x => x.label)))]
    : dirtySections.map(s => s.label);
  const dirtyTabs = new Set(dirtySections.map(s => s.tab));

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    if (errors.length) errorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [errors]);

  const update = (key: string, value: unknown) => setConfig(c => ({ ...c, [key]: value }));
  const setField = (key: string, name: string, value: unknown) =>
    setConfig(c => ({ ...c, [key]: { ...obj(c[key]), [name]: value } }));
  const restore = (keys: string[]) => {
    if (!data) return;
    setConfig(c => ({ ...c, ...Object.fromEntries(keys.map(k => [k, structuredClone(data.config[k])])) }));
    setErrors([]);
  };

  const persist = async () => {
    if (!data) return;
    const found = problems(config, data.extensions);
    setErrors(found);
    if (found.length) { toast.error('请先修正标出的问题'); return; }
    if (!dirtyKeys.length) { toast.info('没有需要保存的修改'); return; }
    const changed = Object.fromEntries(dirtyKeys.map(k => [k, config[k]]));
    setBusy(true);
    try {
      await api.validateConfig(config);
      const r = await api.saveSections(changed, data.revision);
      setData({ ...data, config: r.config, revision: r.revision });
      setConfig(r.config);
      setErrors([]);
      toast.success(extensionsOnly ? `${dirtyLabels.join('、')}已保存，新任务将按新设置执行` : '设置已保存并立即生效');
      if (!extensionsOnly) void resolveDirNames(r.config).then(names => setDirNames(prev => mergeNames(prev, names)));
    } catch (e) {
      const msg = message(e, '保存失败');
      setErrors([msg]);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  /** 保存 AI 起草、用户确认过的配置块（一句话生成规则）；页面没有未保存的修改时才可调用。 */
  const applySections = async (sections: Record<string, unknown>, revision: string) => {
    if (!data) return false;
    setBusy(true);
    try {
      const r = await api.saveSections(sections, revision);
      setData({ ...data, config: r.config, revision: r.revision });
      setConfig(r.config);
      setErrors([]);
      return true;
    } catch (e) {
      toast.error(message(e, '保存失败'));
      return false;
    } finally {
      setBusy(false);
    }
  };

  /** 插件开关即时保存，只提交 plugins，不带上页面上其他未保存的修改。 */
  const toggleExtension = async (x: IExtension, enabled: boolean) => {
    if (!data || busy) return;
    const plugins = { ...obj(data.config.plugins), [x.name]: enabled };
    setConfig(c => ({ ...c, plugins }));
    setBusy(true);
    try {
      await api.validateConfig({ ...data.config, plugins });
      const r = await api.saveSections({ plugins }, data.revision);
      setData({ ...data, config: r.config, revision: r.revision });
      setConfig(c => ({ ...c, plugins: obj(r.config.plugins) }));
      toast.success(`「${x.label}」已${enabled ? '开启' : '关闭'}并立即生效`);
    } catch (e) {
      setConfig(c => ({ ...c, plugins: obj(data.config.plugins) }));
      toast.error(message(e, '设置保存失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page
      title={extensionsOnly ? '插件' : '设置'}
      description={extensionsOnly
        ? '选择各环节的实现方式，开关附加功能并调整参数。开关立即生效，参数修改需保存。'
        : '保存目录、监听来源、确认方式与 115 连接。修改后在页面底部保存，立即生效。'}
      actions={data ? (
        <>
          {!dirty ? (
            <span className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
              <span className="size-1.5 rounded-full bg-success" />所有设置均已生效
            </span>
          ) : null}
          <Button
            variant="outline" size="sm" disabled={busy || dirty}
            title={dirty ? '请先保存或放弃修改' : undefined} onClick={() => void reload()}
          >
            <RefreshCw />重新加载
          </Button>
        </>
      ) : undefined}
    >
      {loadError ? (
        <Notice tone="danger" icon={CircleAlert} title="设置加载失败" className="mb-5"
          action={<Button size="sm" variant="outline" className="bg-card" onClick={() => void reload()}>重试</Button>}>
          {loadError}
        </Notice>
      ) : null}
      {!data && !loadError ? <LoadingState /> : null}

      {data ? (
        <div className="space-y-5">
          {data.config_error ? (
            <Notice tone="danger" icon={CircleAlert} title="配置文件存在错误，保存已暂停">
              {data.config_error}。页面显示的是最后一份可用配置，修复配置文件后点「重新加载」。
            </Notice>
          ) : null}
          {errors.length ? (
            <div ref={errorRef}>
              <Notice tone="danger" icon={CircleAlert} title="设置还没有保存，请先处理以下问题">
                <ul className="list-disc space-y-0.5 pl-4">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
              </Notice>
            </div>
          ) : null}

          {extensionsOnly ? (catalog ? (
            <ExtensionsTab
              catalog={catalog} hooks={hooks} events={events}
              config={config} saved={data.config} setConfig={setConfig} setField={setField}
              busy={busy} onToggle={(x, v) => void toggleExtension(x, v)} onRestore={restore}
            />
          ) : null) : (
            <Tabs value={tab} onValueChange={setTab} className="gap-5">
              <TabsList className="grid h-auto w-full grid-cols-4 rounded-lg bg-muted p-1 sm:inline-flex sm:w-fit">
                {TABS.map(t => (
                  <TabsTrigger key={t.value} value={t.value} className={TRIGGER_CLASS}>
                    {t.label}
                    {dirtyTabs.has(t.value) ? <span className="size-1.5 rounded-full bg-warning" aria-label="有未保存的修改" /> : null}
                  </TabsTrigger>
                ))}
              </TabsList>
              <TabsContent value="common">
                <BasicTab
                  config={config} catalog={catalog} update={update} setField={setField} dirNames={dirNames}
                  rememberDir={(id, name) => setDirNames(prev => ({ ...prev, [id]: name }))}
                  dirty={dirty} applySections={applySections}
                />
              </TabsContent>
              <TabsContent value="sources">
                <SourcesTab config={config} catalog={catalog} update={update} />
              </TabsContent>
              <TabsContent value="advanced">
                <RecognitionTab config={config} setField={setField} />
              </TabsContent>
              <TabsContent value="connections">
                <ConnectionsTab connections={data.connections} />
              </TabsContent>
            </Tabs>
          )}
        </div>
      ) : null}

      {data && dirty ? (
        <div className="sticky bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-20 mt-6 lg:bottom-5">
          <div className="flex items-center gap-3 rounded-xl border border-warning/35 bg-card/95 px-3.5 py-2.5 shadow-lg backdrop-blur animate-in fade-in-0 slide-in-from-bottom-2 sm:px-4 sm:py-3">
            <span className="size-2 shrink-0 rounded-full bg-warning" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-5">有修改尚未保存</p>
              <p className="truncate text-xs leading-5 text-muted-foreground">{dirtyLabels.join('、') || '设置'}</p>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button variant="outline" size="sm" disabled={busy} onClick={() => restore(scopeKeys)}>
                <RotateCcw /><span className="sm:hidden">放弃</span><span className="hidden sm:inline">放弃修改</span>
              </Button>
              <Button size="sm" disabled={busy || Boolean(data.config_error)} onClick={() => void persist()}>
                {busy ? <Loader2 className="animate-spin" /> : <Save />}
                <span className="sm:hidden">保存</span><span className="hidden sm:inline">保存并生效</span>
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </Page>
  );
}
