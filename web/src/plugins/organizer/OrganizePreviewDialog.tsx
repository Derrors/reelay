import { CircleAlert, FilePlay, FolderOpen, FolderRoot, FolderTree, LoaderCircle, Play } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { EmptyState } from '@/components/app/EmptyState';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import type { IOrganizePreviewItem } from './types';

type Kind = 'tv' | 'movie';

interface Recognized {
  title: string; year: number | null; media_type: Kind | null; season: number | null; episodes: number[];
  tmdbid: number | null; tmdb: 'matched' | 'unmatched' | 'unconfigured' | 'failed';
}

const SOURCE_NOTE: Record<Recognized['tmdb'], string> = {
  matched: '已按 TMDB 识别',
  unmatched: 'TMDB 没有可信匹配，按文件名猜测，可手动修改',
  unconfigured: '未配置 TMDB，按文件名猜测，可手动修改',
  failed: 'TMDB 查询失败，按文件名猜测，可手动修改',
};

const KINDS: Array<{ value: Kind; label: string }> = [
  { value: 'tv', label: '剧集' },
  { value: 'movie', label: '电影' },
];

// 像网盘里的原始文件名：带视频后缀，或带 S01E02、1080p 这类标记
function looksLikeFileName(value: string): boolean {
  const text = value.trim();
  return /\.(mkv|mp4|ts|m2ts|avi|mov|wmv|flv|webm|rmvb|iso|mpe?g)$/i.test(text)
    || /(^|[^a-z0-9])(S\d{1,2}E\d{1,4}|2160p|1080p|720p)([^a-z0-9]|$)/i.test(text);
}

function numbers(value: string): number[] {
  return value.split(/[，,\s]+/).map(Number).filter((item) => Number.isInteger(item) && item > 0);
}

function Field({ label, htmlFor, hint, extra, children }: {
  label: string; htmlFor?: string; hint?: ReactNode; extra?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex min-h-5 items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-[13px] font-medium">{label}</label>
        {extra}
      </div>
      {children}
      {hint ? <p className="text-xs leading-5 text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/* ---------------- 目录树 ---------------- */

interface TreeNode {
  name: string;
  dirs: Map<string, TreeNode>;
  files: IOrganizePreviewItem[];
}

function buildTree(items: IOrganizePreviewItem[]): TreeNode {
  const root: TreeNode = { name: '', dirs: new Map(), files: [] };
  for (const item of items) {
    let node = root;
    for (const part of item.target_dir.split('/').filter(Boolean)) {
      let next = node.dirs.get(part);
      if (!next) {
        next = { name: part, dirs: new Map(), files: [] };
        node.dirs.set(part, next);
      }
      node = next;
    }
    node.files.push(item);
  }
  return root;
}

function countDirs(node: TreeNode): number {
  return [...node.dirs.values()].reduce((sum, child) => sum + 1 + countDirs(child), 0);
}

const byName = new Intl.Collator('zh-CN', { numeric: true }).compare;

function TreeChildren({ node }: { node: TreeNode }) {
  const dirs = [...node.dirs.values()].sort((a, b) => byName(a.name, b.name));
  const files = [...node.files].sort((a, b) => byName(a.target_name, b.target_name));
  return (
    <ul className="ml-[9px] space-y-0.5 border-l border-border pl-3">
      {dirs.map((dir) => (
        <li key={dir.name}>
          <div className="flex min-h-8 items-center gap-2 text-[13px] font-medium">
            <FolderOpen className="size-4 shrink-0 text-primary" />
            <span className="min-w-0 break-all">{dir.name}</span>
          </div>
          <TreeChildren node={dir} />
        </li>
      ))}
      {files.map((file) => {
        const renamed = file.src_name !== file.target_name;
        return (
          <li key={file.src_id} className="flex gap-2 rounded-md py-1.5 pr-2">
            <FilePlay className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 text-[13px] leading-5">
              <div className="break-all font-medium text-foreground">{file.target_name}</div>
              <div className="break-all text-xs text-muted-foreground">
                {renamed ? <>原文件名 <span className="font-mono text-[11px]">{file.src_name}</span></> : '文件名不变'}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function PlanTree({ items }: { items: IOrganizePreviewItem[] }) {
  const tree = buildTree(items);
  const dirs = countDirs(tree);
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span><b className="font-semibold tabular-nums text-foreground">{items.length}</b> 个文件</span>
        <span><b className="font-semibold tabular-nums text-foreground">{dirs}</b> 个新文件夹</span>
      </div>
      <div className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 sm:p-4">
        <div className="flex min-h-8 items-center gap-2 text-[13px] text-muted-foreground">
          <FolderRoot className="size-4 shrink-0" />
          保存目录
        </div>
        <TreeChildren node={tree} />
      </div>
    </div>
  );
}

/* ---------------- 弹窗 ---------------- */

interface OrganizePreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 设置里正在编辑的命名模板（可能还没保存）。 */
  naming: { movie?: string; tv?: string };
}

/** 媒体库整理设置里的「预览整理效果」：贴入转存后的文件名，按正在编辑的命名模板看整理成什么样，不碰网盘。
 *  打开时才挂载（见 OrganizerEditor），每次打开都是空白的。 */
export function OrganizePreviewDialog({ open, onOpenChange, naming }: OrganizePreviewDialogProps) {
  const [kind, setKind] = useState<Kind>('tv');
  const [title, setTitle] = useState('');
  const [year, setYear] = useState('');
  const [season, setSeason] = useState('');
  const [episodes, setEpisodes] = useState('');
  const [files, setFiles] = useState('');
  const [items, setItems] = useState<IOrganizePreviewItem[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // 识别结果连同它对应的文件名一起存：文件名变了，旧结果自然失效
  const [recognition, setRecognition] = useState<{ names: string; result: Recognized | null } | null>(null);
  const [pending, setPending] = useState('');
  const [autoRun, setAutoRun] = useState(0);
  const resultRef = useRef<HTMLDivElement>(null);

  const landed = files.split('\n').map((name) => name.trim()).filter(Boolean)
    .map((name, index) => ({ id: `preview-${index + 1}`, name }));

  // 贴文件名后自动识别（与真实整理一样以 TMDB 为准），预填表单并直接生成预览；手动改过的项不再覆盖
  const touched = useRef(new Set<string>());
  const edit = (field: string, set: (value: string) => void) => (value: string) => { touched.current.add(field); set(value); };
  const names = landed.map((f) => f.name).join('\n');
  useEffect(() => {
    if (!names) return;
    let active = true;
    const timer = window.setTimeout(() => {
      setPending(names);
      api.extensionAction<Recognized>('organizer', 'parse_names', { names: names.split('\n') }).then((g) => {
        if (!active) return;
        const fill = (field: string, set: (value: string) => void, value: string) => {
          if (!touched.current.has(field) && value) set(value);
        };
        if (!touched.current.has('kind') && g.media_type) setKind(g.media_type);
        fill('title', setTitle, g.title);
        fill('year', setYear, g.year ? String(g.year) : '');
        fill('season', setSeason, g.season ? String(g.season) : '');
        fill('episodes', setEpisodes, g.episodes.join(', '));
        setRecognition({ names, result: g });
        setAutoRun((n) => n + 1);
      }).catch(() => {
        if (active) setRecognition({ names, result: null });  // 识别不出就让用户自己填
      });
    }, 400);
    return () => { active = false; window.clearTimeout(timer); };
  }, [names]);
  const recognized = recognition?.names === names ? recognition.result : null;
  const recognizing = Boolean(names) && pending === names && recognition?.names !== names;
  // 片名改成别的作品后，识别到的 tmdbid 不再适用
  const tmdbid = recognized?.tmdbid && title.trim() === recognized.title ? recognized.tmdbid : null;
  const missing = landed.length === 0 ? '先在上方贴入至少一个文件名' : !title.trim() ? '再填写片名' : '';
  const ready = !missing;

  const changeTitle = (value: string) => {
    if (!files.trim() && looksLikeFileName(value)) {
      // 原始文件名贴进了片名框：挪到文件名框，片名交给自动识别
      setFiles(value.trim());
      setTitle('');
      touched.current.delete('title');
      toast.info('这是文件名，已移到「转存后的文件名」，片名会自动识别');
      return;
    }
    edit('title', setTitle)(value);
  };

  const preview = async () => {
    if (!ready || busy) return;
    setBusy(true);
    setError('');
    try {
      const tv = kind === 'tv';
      const parsed = {
        is_media: true,
        media_type: kind,
        title: title.trim(),
        year: Number(year) || null,
        season: tv ? Number(season) || null : null,
        episodes: tv ? numbers(episodes) : [],
        raw_fields: tmdbid ? { tmdbid } : {},
      };
      // 媒体库整理插件的动作 preview_plan：按正在编辑的命名模板算出整理后的位置，不碰网盘
      const result = await api.extensionAction<{ items: IOrganizePreviewItem[] }>('organizer', 'preview_plan', { parsed, landed, naming });
      setItems(result.items);
      // 窄屏下结果在表单下方，生成后自动滚过去。
      if (window.matchMedia('(max-width: 1023px)').matches) {
        requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      }
    } catch (err) {
      setItems(null);
      setError(err instanceof Error ? err.message : '无法生成整理方案');
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!autoRun) return;
    // 识别完成、表单填好后自动跑一次；之后改了表单由用户点「生成预览」
    const timer = window.setTimeout(() => void preview(), 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] max-w-5xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="space-y-1 px-5 pb-3 pr-12 pt-5 text-left">
          <DialogTitle className="text-base">预览整理效果</DialogTitle>
          <DialogDescription className="text-xs leading-5">
            贴入转存后的文件名，看按正在编辑的命名模板（包括还没保存的修改）会整理成什么样。只是预览，不会改动网盘。
          </DialogDescription>
        </DialogHeader>
        <div className="grid min-h-0 flex-1 grid-cols-1 items-start gap-5 overflow-y-auto px-5 pb-5 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void preview(); }}>
            <Field
              label="转存后的文件名"
              htmlFor="organize-files"
              hint="每行一个，就是网盘里转存下来的原始文件名；贴进来后自动识别片名与季集并生成预览。"
              extra={<span className="text-xs tabular-nums text-muted-foreground">{landed.length} 个文件</span>}
            >
              <Textarea
                id="organize-files"
                value={files}
                onChange={(e) => setFiles(e.target.value)}
                placeholder={'在这里粘贴文件名，每行一个，例如：\n[死刑将至].Death.is.Coming.2026.S01E25.2160p…mp4'}
                spellCheck={false}
                wrap="off"
                className="min-h-36 resize-y bg-card font-mono text-xs leading-6"
              />
            </Field>
            <Field label="资源类型">
              <div role="radiogroup" aria-label="资源类型" className="grid grid-cols-2 rounded-lg bg-muted p-0.5">
                {KINDS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={kind === option.value}
                    onClick={() => { touched.current.add('kind'); setKind(option.value); }}
                    className={cn(
                      'min-h-8 rounded-md text-sm font-medium transition-colors',
                      kind === option.value ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </Field>
            <Field
              label="片名"
              htmlFor="organize-title"
              extra={recognizing
                ? <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><LoaderCircle className="size-3 animate-spin" />识别中…</span>
                : null}
              hint={recognized && !recognizing
                ? <>{SOURCE_NOTE[recognized.tmdb]}{tmdbid ? <span className="font-mono"> · tmdbid {tmdbid}</span> : null}</>
                : null}
            >
              <Input id="organize-title" className="bg-card" autoComplete="off" value={title} onChange={(e) => changeTitle(e.target.value)} placeholder="整理后的片名，如：死刑将至" />
            </Field>
            <div className={cn('grid gap-3', kind === 'tv' && 'grid-cols-2')}>
              <Field label="年份" htmlFor="organize-year">
                <Input id="organize-year" className="bg-card" inputMode="numeric" value={year} onChange={(e) => edit('year', setYear)(e.target.value.replace(/\D/g, ''))} autoComplete="off" placeholder="选填" />
              </Field>
              {kind === 'tv' ? (
                <Field label="第几季" htmlFor="organize-season">
                  <Input id="organize-season" className="bg-card" inputMode="numeric" value={season} onChange={(e) => edit('season', setSeason)(e.target.value.replace(/\D/g, ''))} autoComplete="off" placeholder="不填为 1" />
                </Field>
              ) : null}
            </div>
            {kind === 'tv' ? (
              <Field label="包含哪几集" htmlFor="organize-episodes" hint="用逗号或空格分隔，例如 1, 2, 3。">
                <Input id="organize-episodes" className="bg-card" value={episodes} autoComplete="off" onChange={(e) => edit('episodes', setEpisodes)(e.target.value)} placeholder="不填则按文件名识别" />
              </Field>
            ) : null}
            <Button type="submit" className="h-10 w-full" disabled={busy || !ready}>
              {busy ? <LoaderCircle className="animate-spin" /> : <Play />}
              {busy ? '正在生成…' : '生成预览'}
            </Button>
            {missing ? <p className="-mt-1 text-center text-xs text-muted-foreground">{missing}，即可生成预览</p> : null}
          </form>

          <div ref={resultRef} className="scroll-mt-4 rounded-xl border border-border bg-card p-4">
            <div className="mb-3">
              <h3 className="text-[13px] font-medium">整理后的结构</h3>
              <p className="mt-0.5 text-xs text-muted-foreground">按正在编辑的命名模板，符合 Jellyfin / Emby 刮削规范</p>
            </div>
            {error ? (
              <EmptyState icon={CircleAlert} title="无法生成整理方案" description={error} className="border-none py-12" />
            ) : items === null ? (
              <EmptyState
                icon={FolderTree}
                title="填好资源信息后生成预览"
                description="会列出整理后的文件夹层级和每个文件的新名字；不会执行任何转存、移动或改名。"
                className="border-none py-12"
              />
            ) : items.length === 0 ? (
              <EmptyState icon={FolderTree} title="没有需要整理的文件" description="命名规则没有为这些文件生成新位置，请检查文件名或资源类型。" className="border-none py-12" />
            ) : (
              <div className={cn('transition-opacity', busy && 'opacity-50')}>
                <PlanTree items={items} />
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
