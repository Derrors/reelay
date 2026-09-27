import { useState, type Dispatch, type SetStateAction } from 'react';
import { ArrowDown, ArrowUp, ExternalLink, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type { IExtension } from '@/lib/types';

type Obj = Record<string, unknown>;
type Categories = Record<string, Record<string, Record<string, string>>>;
const obj = (v: unknown): Obj => v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : {};
const text = (v: unknown) => v == null ? '' : String(v);
const defaults: Categories = {
  movie: {
    '动漫/动画电影': { genre_ids: '16' }, '电影/港台电影': { origin_country: 'TW,HK' },
    '电影/国产电影': { origin_country: 'CN' }, '电影/日韩电影': { origin_country: 'JP,KP,KR' }, '电影/欧美电影': {},
  },
  tv: {
    '动漫/国产动漫': { genre_ids: '16', origin_country: 'CN,TW,HK' },
    '动漫/欧美动漫': { genre_ids: '16', origin_country: 'US,FR,GB,DE,ES,IT,NL,PT,RU,UK' },
    '动漫/日本番剧': { genre_ids: '16', origin_country: 'JP' }, '其他/纪录影片': { genre_ids: '99' },
    '其他/综艺节目': { genre_ids: '10764,10767' }, '剧集/港台剧集': { origin_country: 'TW,HK' },
    '剧集/国产剧集': { origin_country: 'CN' }, '剧集/日韩剧集': { origin_country: 'JP,KP,KR' },
    '剧集/南亚剧集': { origin_country: 'TH,IN,SG' },
    '剧集/欧美剧集': { origin_country: 'US,FR,GB,DE,ES,IT,NL,PT,RU,UK' },
  },
};
function at(root: Obj, key: string) { return obj(root[key]); }

export default function PluginConfigEditor({ extension, config, setConfig }:
  { extension: IExtension; config: Obj; setConfig: Dispatch<SetStateAction<Obj>> }) {
  if (extension.config_type === 'naming_templates') return <NamingEditor config={config} setConfig={setConfig} />;
  if (extension.config_type === 'category_strategy') return <CategoryEditor config={config} setConfig={setConfig} />;
  return null;
}

function NamingEditor({ config, setConfig }: { config: Obj; setConfig: Dispatch<SetStateAction<Obj>> }) {
  const naming = at(at(config, 'organize'), 'naming');
  const [preview, setPreview] = useState<Record<string, string>>({});
  const update = (kind: string, value: string) => setConfig(c => ({ ...c, organize: { ...at(c, 'organize'), naming: { ...at(at(c, 'organize'), 'naming'), [kind]: value } } }));
  const test = async (kind: 'movie' | 'tv') => {
    try { const r = await api.organizerTemplatePreview(kind, text(naming[kind])); setPreview(p => ({ ...p, [kind]: r.path })); }
    catch (e) { toast.error(e instanceof Error ? e.message : '模板预览失败'); }
  };
  return <div className="space-y-4 border-t pt-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><b className="text-sm">Jinja2 命名模板</b><a href="https://jinja.palletsprojects.com/en/3.0.x/templates/" target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-primary underline">Jinja2 语法参考<ExternalLink className="size-3" /></a></div>
    <p className="text-xs text-muted-foreground">支持 <code>{'{{ variable }}'}</code>、<code>{'{% if %}'}</code>、<code>{'{% for %}'}</code>；允许 default/lower/upper/replace/trim/join/length/pad2 过滤器。禁止 import、include、macro、任意函数调用，渲染后还会校验路径安全。</p>
    {(['movie', 'tv'] as const).map(kind => <label key={kind} className="block space-y-2 text-sm"><b>{kind === 'movie' ? '电影' : '剧集'}模板</b><Textarea aria-label={`${kind} Jinja2模板`} className="min-h-24 font-mono text-xs" value={text(naming[kind])} onChange={e => update(kind, e.target.value)} /><div className="flex items-center justify-between gap-3"><span className="truncate font-mono text-xs text-muted-foreground">{preview[kind] ? `示例：${preview[kind]}` : '点击预览会使用示例媒体数据执行安全渲染'}</span><Button type="button" variant="outline" size="sm" onClick={() => void test(kind)}>校验并预览</Button></div></label>)}
    <p className="text-xs text-muted-foreground">变量：title、year、resolution、ext、season、episode（兼容 ep）、media_type、genre_ids、origin_country、tmdb_id、original_name。</p>
  </div>;
}

function CategoryEditor({ config, setConfig }: { config: Obj; setConfig: Dispatch<SetStateAction<Obj>> }) {
  const categories = at(at(config, 'dir_router'), 'categories') as Categories;
  const [sample, setSample] = useState({ media_type: 'movie', genre_ids: '16', origin_country: 'JP' });
  const [result, setResult] = useState('');
  const updateAll = (value: Categories) => setConfig(c => ({ ...c, dir_router: { ...at(c, 'dir_router'), categories: value } }));
  const entries = (kind: string) => Object.entries(obj(categories?.[kind])) as [string, Record<string, string>][];
  const updateEntry = (kind: string, index: number, nextPath: string, conditions: Record<string, string>) => {
    const list = entries(kind); list[index] = [nextPath, conditions]; updateAll({ ...categories, [kind]: Object.fromEntries(list) });
  };
  const move = (kind: string, index: number, delta: number) => {
    const list = entries(kind); const target = index + delta; if (target < 0 || target >= list.length) return;
    [list[index], list[target]] = [list[target], list[index]]; updateAll({ ...categories, [kind]: Object.fromEntries(list) });
  };
  const remove = (kind: string, index: number) => updateAll({ ...categories, [kind]: Object.fromEntries(entries(kind).filter((_, n) => n !== index)) });
  const add = (kind: string) => {
    const list = entries(kind); const fallback = list.findIndex(([, c]) => !Object.keys(c).length);
    list.splice(fallback < 0 ? list.length : fallback, 0, [`${kind === 'movie' ? '电影' : '剧集'}/新分类`, { origin_country: 'CN' }]);
    updateAll({ ...categories, [kind]: Object.fromEntries(list) });
  };
  const preview = async () => {
    try {
      const genre_ids = sample.genre_ids.split(',').filter(Boolean).map(Number);
      const origin_country = sample.origin_country.split(',').map(x => x.trim().toUpperCase()).filter(Boolean);
      const r = await api.dirRouterPreview(categories as unknown as Record<string, unknown>, sample.media_type, genre_ids, origin_country);
      setResult(r.path || '未命中分类');
    } catch (e) { toast.error(e instanceof Error ? e.message : '分类预览失败'); }
  };
  return <div className="space-y-5 border-t pt-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><b className="text-sm">二级分类策略</b><p className="mt-1 text-xs text-muted-foreground">依次匹配；同一分类内 genre_ids 与 origin_country 为“并且”，字段内逗号值为“或”。空条件是兜底，必须放最后。</p></div><Button type="button" size="sm" variant="outline" onClick={() => updateAll(structuredClone(defaults))}><RotateCcw className="size-4" />恢复 MoviePilot 默认策略</Button></div>
    {(['movie', 'tv'] as const).map(kind => <div key={kind} className="space-y-3"><div className="flex items-center justify-between"><h3 className="font-medium">{kind === 'movie' ? '电影分类' : '电视剧分类'}</h3><Button size="sm" variant="outline" onClick={() => add(kind)}><Plus className="size-4" />添加分类</Button></div>{entries(kind).map(([path, conditions], i) => <div key={`${path}-${i}`} className="grid items-end gap-2 rounded border p-3 md:grid-cols-[1.4fr_1fr_1.5fr_auto]"><label className="space-y-1 text-xs"><span>一级/二级目录</span><Input value={path} onChange={e => updateEntry(kind, i, e.target.value, conditions)} /></label><label className="space-y-1 text-xs"><span>genre_ids</span><Input value={text(conditions.genre_ids)} placeholder="如 16" onChange={e => updateEntry(kind, i, path, { ...conditions, genre_ids: e.target.value })} /></label><label className="space-y-1 text-xs"><span>origin_country</span><Input value={text(conditions.origin_country)} placeholder="如 CN,TW,HK" onChange={e => updateEntry(kind, i, path, { ...conditions, origin_country: e.target.value })} /></label><div className="flex"><Button size="icon" variant="ghost" disabled={i === 0} onClick={() => move(kind, i, -1)} aria-label={`上移${path}`}><ArrowUp className="size-4" /></Button><Button size="icon" variant="ghost" disabled={i === entries(kind).length - 1} onClick={() => move(kind, i, 1)} aria-label={`下移${path}`}><ArrowDown className="size-4" /></Button><Button size="icon" variant="ghost" onClick={() => remove(kind, i)} aria-label={`删除${path}`}><Trash2 className="size-4" /></Button></div><label className="md:col-span-4 flex items-center gap-2 text-xs"><input type="checkbox" checked={!Object.keys(conditions).length} onChange={e => updateEntry(kind, i, path, e.target.checked ? {} : { origin_country: 'CN' })} />未命中其它分类时兜底</label></div>)}</div>)}
    <div className="space-y-3 rounded border border-dashed p-4"><b className="text-sm">分类预览</b><div className="grid gap-2 sm:grid-cols-3"><select aria-label="预览媒体类型" className="h-10 rounded border bg-background px-3" value={sample.media_type} onChange={e => setSample(s => ({ ...s, media_type: e.target.value }))}><option value="movie">movie</option><option value="tv">tv</option></select><Input aria-label="预览genre_ids" value={sample.genre_ids} onChange={e => setSample(s => ({ ...s, genre_ids: e.target.value }))} placeholder="genre_ids，如 16" /><Input aria-label="预览origin_country" value={sample.origin_country} onChange={e => setSample(s => ({ ...s, origin_country: e.target.value }))} placeholder="国家/地区，如 JP" /></div><div className="flex items-center justify-between"><span className="text-sm">命中结果：<b>{result || '尚未预览'}</b></span><Button type="button" variant="outline" onClick={() => void preview()}>校验并预览</Button></div></div>
  </div>;
}
