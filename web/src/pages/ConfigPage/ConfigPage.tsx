import { useEffect, useState } from 'react';
import { CheckCircle2, CircleDashed, Plus, Save, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import type { IConfigResponse } from '@/lib/types';
import { connectionLabels } from '@/lib/status';

type ConfigObject = Record<string, unknown>;
type SourceConfig = { bot: string; approval_mode?: string; save_to?: string; parser?: string };

function asObject(value: unknown): ConfigObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as ConfigObject : {};
}

export default function ConfigPage() {
  const [data, setData] = useState<IConfigResponse | null>(null);
  const [config, setConfig] = useState<ConfigObject>({});
  const [rawText, setRawText] = useState('');
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const result = await api.config();
      setData(result);
      setConfig(result.config);
      setRawText(result.text);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '读取配置失败');
    }
  };

  useEffect(() => {
    let active = true;
    api.config().then((result) => {
      if (!active) return;
      setData(result);
      setConfig(result.config);
      setRawText(result.text);
    }).catch((err: unknown) => {
      if (active) toast.error(err instanceof Error ? err.message : '读取配置失败');
    });
    return () => { active = false; };
  }, []);

  const saveStructured = async () => {
    setSaving(true);
    try {
      await api.saveConfig(config);
      toast.success('配置已保存；后台将重载，新任务使用新策略');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存配置失败');
    } finally {
      setSaving(false);
    }
  };

  const saveRaw = async () => {
    setSaving(true);
    try {
      const result = await api.saveConfigText(rawText);
      setConfig(result.config);
      setRawText(result.text);
      toast.success('高级配置已保存；后台将重载，新任务使用新策略');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存配置失败');
    } finally {
      setSaving(false);
    }
  };

  const defaults = asObject(config.defaults);
  const forward = asObject(config.forward);
  const saveDirs = asObject(config.save_dirs);
  const sources = Array.isArray(config.sources) ? config.sources as SourceConfig[] : [];

  const updateSection = (section: string, key: string, value: unknown) => {
    setConfig((current) => ({ ...current, [section]: { ...asObject(current[section]), [key]: value } }));
  };

  const updateSource = (index: number, patch: Partial<SourceConfig>) => {
    setConfig((current) => ({
      ...current,
      sources: (Array.isArray(current.sources) ? current.sources : []).map((item, itemIndex) => itemIndex === index ? { ...asObject(item), ...patch } : item),
    }));
  };

  return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader eyebrow="规则配置" title="来源、审批、转发与目录" description="常用字段使用表单维护；高级编辑保留完整 YAML/JSON，因此未知插件字段不会被表单无声删除。敏感凭证只从环境变量读取，不在页面回显。" />

      {data?.config_error ? <p className="mt-5 border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{data.config_error}。请在高级配置中修正并保存。</p> : null}

      <Tabs defaultValue="common" className="mt-6 gap-5">
        <TabsList><TabsTrigger value="common">常用设置</TabsTrigger><TabsTrigger value="advanced">高级配置</TabsTrigger><TabsTrigger value="connections">连接状态</TabsTrigger></TabsList>

        <TabsContent value="common" className="space-y-5">
          <div className="grid gap-5 xl:grid-cols-2">
            <Card className="rounded-md shadow-none"><CardHeader><CardTitle className="text-base">默认策略</CardTitle><CardDescription>单个来源未覆盖时使用。</CardDescription></CardHeader><CardContent className="grid gap-4 sm:grid-cols-2">
              <label className="text-xs text-muted-foreground">审批模式<NativeSelect value={String(defaults.approval_mode ?? 'manual')} onChange={(event) => updateSection('defaults', 'approval_mode', event.target.value)} className="mt-1 w-full"><NativeSelectOption value="manual">人工确认</NativeSelectOption><NativeSelectOption value="auto">自动审批并通知</NativeSelectOption><NativeSelectOption value="default_pass">静默通过</NativeSelectOption></NativeSelect></label>
              <label className="text-xs text-muted-foreground">默认目录<Input value={String(defaults.save_to ?? '默认')} onChange={(event) => updateSection('defaults', 'save_to', event.target.value)} className="mt-1" /></label>
              <label className="text-xs text-muted-foreground">解析器<Input value={String(defaults.parser ?? 'llm')} onChange={(event) => updateSection('defaults', 'parser', event.target.value)} className="mt-1" /></label>
              <label className="text-xs text-muted-foreground">收藏决策<NativeSelect value={String(defaults.decision ?? 'default')} onChange={(event) => updateSection('defaults', 'decision', event.target.value)} className="mt-1 w-full"><NativeSelectOption value="default">规则与审批模式</NativeSelectOption><NativeSelectOption value="llm">自然语言偏好</NativeSelectOption></NativeSelect></label>
              <label className="text-xs text-muted-foreground sm:col-span-2">收藏偏好<Textarea value={String(defaults.preferences ?? '')} onChange={(event) => updateSection('defaults', 'preferences', event.target.value)} placeholder="例如：只收 4K 电影，动画存入动漫目录" className="mt-1 min-h-20" /></label>
              <p className="text-xs text-muted-foreground sm:col-span-2">自然语言偏好需选择对应决策方式。AI 异常或低置信度会转人工，人工审批模式仍需确认。</p>
            </CardContent></Card>

            <Card className="rounded-md shadow-none"><CardHeader><CardTitle className="text-base">识别后转发</CardTitle><CardDescription>识别为影视资源后按模板转发；执行阶段由通知 Provider 决定。</CardDescription></CardHeader><CardContent className="space-y-4">
              <div className="flex items-center justify-between"><span className="text-sm">启用转发</span><Switch checked={Boolean(forward.enabled)} onCheckedChange={(checked) => updateSection('forward', 'enabled', checked)} /></div>
              <label className="text-xs text-muted-foreground">目标 Bot / 频道<Input value={String(forward.target ?? '')} onChange={(event) => updateSection('forward', 'target', event.target.value)} className="mt-1" /></label>
              <label className="text-xs text-muted-foreground">消息模板<Textarea value={String(forward.template ?? '')} onChange={(event) => updateSection('forward', 'template', event.target.value)} className="mt-1 min-h-24 font-mono text-xs" /></label>
            </CardContent></Card>
          </div>

          <Card className="rounded-md shadow-none"><CardHeader className="flex-row items-start justify-between"><div><CardTitle className="text-base">监听来源</CardTitle><CardDescription>不同 Bot 可覆盖审批模式、目标目录和解析器。</CardDescription></div><Button size="sm" variant="outline" onClick={() => setConfig((current) => ({ ...current, sources: [...(Array.isArray(current.sources) ? current.sources : []), { bot: '' }] }))}><Plus className="size-4" />添加来源</Button></CardHeader><CardContent className="space-y-3">
            {sources.length === 0 ? <div className="border border-dashed p-6 text-center text-sm text-muted-foreground">尚未配置监听来源</div> : sources.map((source, index) => (
              <div key={`${source.bot}-${index}`} className="grid gap-3 border-b pb-4 last:border-b-0 sm:grid-cols-[1.4fr_1fr_1fr_1fr_auto]">
                <Input aria-label="Bot 用户名" placeholder="@resource_bot" value={source.bot ?? ''} onChange={(event) => updateSource(index, { bot: event.target.value })} />
                <NativeSelect aria-label="审批模式" value={source.approval_mode ?? String(defaults.approval_mode ?? 'manual')} onChange={(event) => updateSource(index, { approval_mode: event.target.value })} className="w-full"><NativeSelectOption value="manual">人工确认</NativeSelectOption><NativeSelectOption value="auto">自动审批并通知</NativeSelectOption><NativeSelectOption value="default_pass">静默通过</NativeSelectOption></NativeSelect>
                <Input aria-label="目标目录" placeholder="目录别名" value={source.save_to ?? String(defaults.save_to ?? '默认')} onChange={(event) => updateSource(index, { save_to: event.target.value })} />
                <Input aria-label="解析器" placeholder="llm" value={source.parser ?? String(defaults.parser ?? 'llm')} onChange={(event) => updateSource(index, { parser: event.target.value })} />
                <Button aria-label="删除来源" size="icon" variant="ghost" onClick={() => setConfig((current) => ({ ...current, sources: (Array.isArray(current.sources) ? current.sources : []).filter((_, itemIndex) => itemIndex !== index) }))}><Trash2 className="size-4" /></Button>
              </div>
            ))}
          </CardContent></Card>

          <Card className="rounded-md shadow-none"><CardHeader><CardTitle className="text-base">115 目录映射</CardTitle><CardDescription>目录别名映射到 115 cid；切换规则只引用别名。</CardDescription></CardHeader><CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {Object.entries(saveDirs).map(([alias, cid]) => <label key={alias} className="text-xs text-muted-foreground">{alias}<Input value={String(cid)} onChange={(event) => setConfig((current) => ({ ...current, save_dirs: { ...asObject(current.save_dirs), [alias]: event.target.value } }))} className="mt-1 font-mono" /></label>)}
          </CardContent></Card>

          <div className="flex justify-end"><Button disabled={saving} onClick={() => void saveStructured()}><Save className="size-4" />保存常用设置</Button></div>
        </TabsContent>

        <TabsContent value="advanced">
          <Card className="rounded-md shadow-none"><CardHeader><CardTitle className="text-base">完整 YAML / JSON</CardTitle><CardDescription>保存前由后端校验结构并原子替换配置文件。JSON 是合法 YAML 子集，可在未安装 PyYAML 的最小环境使用。</CardDescription></CardHeader><CardContent><Textarea value={rawText} onChange={(event) => setRawText(event.target.value)} spellCheck={false} className="min-h-[520px] resize-y font-mono text-xs leading-6" /><div className="mt-4 flex justify-end"><Button disabled={saving} onClick={() => void saveRaw()}><Save className="size-4" />校验并保存</Button></div></CardContent></Card>
        </TabsContent>

        <TabsContent value="connections">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {data ? Object.entries(data.connections).map(([key, connection]) => <Card key={key} className="rounded-md shadow-none"><CardContent className="flex items-center justify-between p-5"><div><div className="text-sm font-medium">{connection.label}</div><div className="mt-1 text-xs text-muted-foreground">凭证不会在页面回显</div></div><span className={`flex items-center gap-1.5 text-xs ${connection.status === 'connected' ? 'text-success' : 'text-muted-foreground'}`}>{connection.status === 'connected' ? <CheckCircle2 className="size-4" /> : <CircleDashed className="size-4" />}{connectionLabels[connection.status] ?? '待验证'}</span></CardContent></Card>) : <div className="text-sm text-muted-foreground">正在读取连接配置…</div>}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
