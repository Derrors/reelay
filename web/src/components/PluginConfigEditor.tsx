import { useState, type Dispatch, type SetStateAction } from 'react';
import { ArrowDown, ArrowUp, ExternalLink, Eye, FlaskConical, Loader2, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { Select } from '@/components/app/Select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import type { IExtension } from '@/lib/types';
import { cn } from '@/lib/utils';

type Obj = Record<string, unknown>;
type Conditions = Record<string, string>;
type Categories = Record<string, Record<string, Conditions>>;
type Kind = 'movie' | 'tv';
const obj = (v: unknown): Obj => v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : {};
const text = (v: unknown) => v == null ? '' : String(v);
const KIND_LABEL: Record<Kind, string> = { movie: '电影', tv: '剧集' };
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
const NAMING_VARS: Array<[string, string]> = [
  ['title', '片名'], ['year', '年份'], ['resolution', '清晰度'], ['ext', '后缀'], ['season', '季'], ['episode', '集'],
  ['media_type', '类型'], ['genre_ids', '题材'], ['origin_country', '地区'], ['tmdb_id', 'TMDB 编号'],
];
function at(root: Obj, key: string) { return obj(root[key]); }

type SectionProps = { section: Obj; setSection: (update: (section: Obj) => Obj) => void };

/** 插件自己的设置在 plugins.<名> 下：编辑器只读写这一段。 */
export default function PluginConfigEditor({ extension, config, setConfig }:
  { extension: IExtension; config: Obj; setConfig: Dispatch<SetStateAction<Obj>> }) {
  const section = at(at(config, 'plugins'), extension.name);
  const setSection = (update: (section: Obj) => Obj) => setConfig(c => {
    const plugins = at(c, 'plugins');
    return { ...c, plugins: { ...plugins, [extension.name]: update(at(plugins, extension.name)) } };
  });
  if (extension.config_type === 'naming_templates') return <NamingEditor section={section} setSection={setSection} />;
  if (extension.config_type === 'category_strategy') return <CategoryEditor section={section} setSection={setSection} />;
  return null;
}

function EditorHeading({ title, description, action }: { title: string; description: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <p className="text-[13px] font-medium leading-5">{title}</p>
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

function NamingEditor({ section, setSection }: SectionProps) {
  const naming = at(section, 'naming');
  const [preview, setPreview] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState<Kind | null>(null);
  const update = (kind: Kind, value: string) => {
    setSection(s => ({ ...s, naming: { ...at(s, 'naming'), [kind]: value } }));
    setPreview(p => ({ ...p, [kind]: '' }));
  };
  const test = async (kind: Kind) => {
    setTesting(kind);
    try { const r = await api.organizerTemplatePreview(kind, text(naming[kind])); setPreview(p => ({ ...p, [kind]: r.path })); }
    catch (e) { toast.error(e instanceof Error ? e.message : '模板预览失败'); }
    finally { setTesting(null); }
  };
  return (
    <div className="space-y-4">
      <EditorHeading
        title="文件命名模板"
        description={<>用占位符拼出整理后的目录与文件名，如 <code className="rounded bg-muted px-1 font-mono text-[11px]">{'{{ title }} ({{ year }})'}</code>；保存前会自动检查模板，不会改动网盘里已有的文件。</>}
        action={(
          <a href="https://jinja.palletsprojects.com/en/3.0.x/templates/" target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
            模板语法说明<ExternalLink className="size-3" />
          </a>
        )}
      />
      {(['movie', 'tv'] as const).map(kind => (
        <div key={kind} className="space-y-2.5 rounded-lg border border-border bg-card p-3 sm:p-4">
          <label className="block space-y-1.5">
            <span className="block text-[13px] font-medium">{KIND_LABEL[kind]}</span>
            <Textarea
              aria-label={`${KIND_LABEL[kind]}命名模板`} spellCheck={false}
              className="min-h-20 break-all font-mono text-xs leading-5 md:text-xs"
              value={text(naming[kind])} onChange={e => update(kind, e.target.value)}
            />
          </label>
          <div className="flex items-start gap-2">
            <div className={cn(
              'min-h-8 min-w-0 flex-1 rounded-md bg-muted/70 px-3 py-1.5 text-xs leading-5',
              preview[kind] ? 'break-all font-mono text-foreground' : 'text-muted-foreground',
            )}>
              {preview[kind] || '点「预览」，用示例影片查看改名效果'}
            </div>
            <Button type="button" variant="outline" size="sm" className="shrink-0" disabled={testing === kind} onClick={() => void test(kind)}>
              {testing === kind ? <Loader2 className="animate-spin" /> : <Eye />}预览
            </Button>
          </div>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        <span>可用信息：</span>
        {NAMING_VARS.map(([key, label]) => (
          <span key={key} className="rounded-md border border-border bg-card px-1.5 py-0.5">
            {label} <code className="font-mono text-[11px] text-foreground">{key}</code>
          </span>
        ))}
      </div>
    </div>
  );
}

const PATH_PATTERN = /^[^/]+\/[^/]+$/;

/** 分类路径输入：重名或为空时只保留在输入框里，不写回配置（对象键重名会吞掉另一条分类）。 */
function PathInput({ value, taken, label, onCommit }: { value: string; taken: string[]; label: string; onCommit: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  const [synced, setSynced] = useState(value);
  if (value !== synced) { setSynced(value); setDraft(value); }
  const duplicate = draft !== value && taken.includes(draft);
  const invalid = duplicate || !draft.trim() || !PATH_PATTERN.test(draft);
  return (
    <div className="min-w-0 space-y-1">
      <Input
        aria-label={label} aria-invalid={invalid} className="bg-card" value={draft} placeholder="一级/二级，如 电影/国产电影"
        onChange={e => {
          const next = e.target.value;
          setDraft(next);
          if (next.trim() && !taken.includes(next)) { setSynced(next); onCommit(next); }
        }}
        onBlur={() => { if (draft !== value) setDraft(value); }}
      />
      {invalid ? (
        <p className="text-[11px] leading-4 text-destructive">
          {duplicate ? '已有同名分类' : !draft.trim() ? '不能为空' : '需为「一级/二级」格式'}
        </p>
      ) : null}
    </div>
  );
}

const ROW_COLS = 'md:grid-cols-[1.5rem_minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1.2fr)_4.5rem_6.5rem]';

function CategoryEditor({ section, setSection }: SectionProps) {
  const categories = at(section, 'categories') as Categories;
  const [sample, setSample] = useState({ media_type: 'movie', genre_ids: '16', origin_country: 'JP' });
  const [result, setResult] = useState('');
  const [testing, setTesting] = useState(false);
  const updateAll = (value: Categories) => {
    setSection(s => ({ ...s, categories: value }));
    setResult('');
  };
  const entries = (kind: Kind) => Object.entries(obj(categories?.[kind])) as [string, Conditions][];
  const write = (kind: Kind, list: [string, Conditions][]) => updateAll({ ...categories, [kind]: Object.fromEntries(list) });
  const updateEntry = (kind: Kind, index: number, path: string, conditions: Conditions) => {
    const list = entries(kind); list[index] = [path, conditions]; write(kind, list);
  };
  const setCondition = (kind: Kind, index: number, key: string, value: string) => {
    const [path, conditions] = entries(kind)[index];
    const next = { ...conditions };
    // 留空即不限制该条件；后端不接受空字符串
    if (value.trim()) next[key] = value; else delete next[key];
    updateEntry(kind, index, path, next);
  };
  const move = (kind: Kind, index: number, delta: number) => {
    const list = entries(kind); const target = index + delta;
    if (target < 0 || target >= list.length) return;
    [list[index], list[target]] = [list[target], list[index]];
    write(kind, list);
  };
  const remove = (kind: Kind, index: number) => write(kind, entries(kind).filter((_, n) => n !== index));
  const add = (kind: Kind) => {
    const list = entries(kind);
    const names = new Set(list.map(([p]) => p));
    const base = `${KIND_LABEL[kind]}/新分类`;
    let name = base;
    for (let n = 2; names.has(name); n++) name = `${base}${n}`;
    const fallback = list.findIndex(([, c]) => !Object.keys(c).length);
    list.splice(fallback < 0 ? list.length : fallback, 0, [name, { origin_country: 'CN' }]);
    write(kind, list);
  };
  const preview = async () => {
    setTesting(true);
    try {
      const genre_ids = sample.genre_ids.split(',').map(x => x.trim()).filter(Boolean).map(Number);
      const origin_country = sample.origin_country.split(',').map(x => x.trim().toUpperCase()).filter(Boolean);
      const r = await api.dirRouterPreview(categories as unknown as Record<string, unknown>, sample.media_type, genre_ids, origin_country);
      setResult(r.path || '没有命中任何分类');
    } catch (e) { toast.error(e instanceof Error ? e.message : '分类预览失败'); }
    finally { setTesting(false); }
  };

  return (
    <div className="space-y-5">
      <EditorHeading
        title="自动分类到不同文件夹"
        description="从上到下依次匹配，题材与地区都满足才归入；留空表示不限制。全部不匹配时放进兜底分类。常用题材编号：16 动画、99 纪录、10764 真人秀、10767 脱口秀。"
        action={<Button type="button" size="sm" variant="outline" onClick={() => updateAll(structuredClone(defaults))}><RotateCcw />恢复默认分类</Button>}
      />
      {(['movie', 'tv'] as const).map(kind => {
        const list = entries(kind);
        const misplaced = list.some(([, c], i) => !Object.keys(c).length && i !== list.length - 1);
        return (
          <div key={kind} className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <h4 className="text-[13px] font-semibold">{KIND_LABEL[kind]}<span className="ml-1.5 font-normal tabular-nums text-muted-foreground">{list.length} 个分类</span></h4>
              <Button size="sm" variant="outline" onClick={() => add(kind)}><Plus />添加分类</Button>
            </div>
            <div className="rounded-lg md:border md:border-border md:bg-card md:px-3">
              <div className={cn('hidden gap-3 border-b border-border/70 py-2 text-xs font-medium text-muted-foreground md:grid', ROW_COLS)}>
                <span>#</span><span>归入的文件夹</span><span>题材编号</span><span>国家/地区代码</span><span>兜底</span><span className="sr-only">操作</span>
              </div>
              <div className="space-y-2 md:space-y-0 md:divide-y md:divide-border/60">
                {list.map(([path, conditions], i) => {
                  const fallback = !Object.keys(conditions).length;
                  return (
                    <div key={`${kind}-${i}`} className={cn(
                      'grid grid-cols-2 gap-2 rounded-lg border border-border bg-card p-3 md:items-start md:gap-3 md:rounded-none md:border-0 md:bg-transparent md:px-0 md:py-2',
                      ROW_COLS,
                    )}>
                      <span className="hidden pt-2 text-xs tabular-nums text-muted-foreground md:block">{i + 1}</span>
                      <div className="col-span-2 min-w-0 space-y-1 md:col-span-1">
                        <span className="block text-xs text-muted-foreground md:hidden">归入的文件夹</span>
                        <PathInput
                          value={path} label={`${KIND_LABEL[kind]}分类 ${i + 1} 的文件夹`}
                          taken={list.filter((_, n) => n !== i).map(([p]) => p)}
                          onCommit={next => updateEntry(kind, i, next, conditions)}
                        />
                      </div>
                      <label className="min-w-0 space-y-1">
                        <span className="block text-xs text-muted-foreground md:sr-only">题材编号</span>
                        <Input className="bg-card" value={text(conditions.genre_ids)} placeholder="不限" onChange={e => setCondition(kind, i, 'genre_ids', e.target.value)} />
                      </label>
                      <label className="min-w-0 space-y-1">
                        <span className="block text-xs text-muted-foreground md:sr-only">国家/地区</span>
                        <Input className="bg-card" value={text(conditions.origin_country)} placeholder="不限，如 CN,JP" onChange={e => setCondition(kind, i, 'origin_country', e.target.value)} />
                      </label>
                      <div className="col-span-2 flex items-center justify-between gap-2 md:contents">
                        <label className="flex h-9 items-center gap-2 text-xs text-muted-foreground">
                          <Switch
                            checked={fallback} aria-label={`将「${path}」设为兜底分类`}
                            onCheckedChange={v => updateEntry(kind, i, path, v ? {} : { origin_country: 'CN' })}
                          />
                          <span className="md:sr-only">兜底分类</span>
                        </label>
                        <div className="flex justify-end">
                          <Button size="icon" variant="ghost" className="size-8" disabled={i === 0} onClick={() => move(kind, i, -1)} aria-label={`上移${path}`}><ArrowUp /></Button>
                          <Button size="icon" variant="ghost" className="size-8" disabled={i === list.length - 1} onClick={() => move(kind, i, 1)} aria-label={`下移${path}`}><ArrowDown /></Button>
                          <Button size="icon" variant="ghost" className="size-8 hover:bg-destructive/10 hover:text-destructive" onClick={() => remove(kind, i)} aria-label={`删除${path}`}><Trash2 /></Button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            {misplaced ? <p className="text-xs text-warning">兜底分类只能有一个，并且要放在最后。</p> : null}
          </div>
        );
      })}

      <div className="rounded-lg border border-dashed border-border bg-card/60 p-3 sm:p-4">
        <EditorHeading title="试一试" description="填一部假想作品的信息，看看按当前（含未保存的）分类会被放到哪里。" />
        <div className="mt-3 grid gap-2 sm:grid-cols-[8rem_minmax(0,1fr)_minmax(0,1fr)_auto]">
          <Select
            aria-label="测试媒体类型" value={sample.media_type}
            onValueChange={v => setSample(s => ({ ...s, media_type: v }))}
            options={[{ value: 'movie', label: '电影' }, { value: 'tv', label: '剧集' }]}
          />
          <Input aria-label="测试题材编号" className="bg-card" value={sample.genre_ids} onChange={e => setSample(s => ({ ...s, genre_ids: e.target.value }))} placeholder="题材编号，如 16" />
          <Input aria-label="测试国家地区" className="bg-card" value={sample.origin_country} onChange={e => setSample(s => ({ ...s, origin_country: e.target.value }))} placeholder="国家/地区代码，如 JP" />
          <Button type="button" variant="outline" disabled={testing} onClick={() => void preview()}>
            {testing ? <Loader2 className="animate-spin" /> : <FlaskConical />}测试
          </Button>
        </div>
        <p className="mt-3 text-sm">
          将归入：<b className={result ? 'text-primary' : 'font-normal text-muted-foreground'}>{result || '还未测试'}</b>
        </p>
      </div>
    </div>
  );
}
