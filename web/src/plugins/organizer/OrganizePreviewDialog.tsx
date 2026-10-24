import { CircleAlert, FilePlay, FolderOpen, FolderRoot, FolderTree, LoaderCircle, Play } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { configApi } from '@/api/config';
import { EmptyState } from '@/components/app/EmptyState';
import { Field } from '@/components/app/Field';
import { Segmented } from '@/components/app/Segmented';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAsync } from '@/hooks/useAsync';
import { MEDIA_TYPE_LABELS } from '@/lib/status';
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

const KINDS = (['tv', 'movie'] as const).map((value) => ({ value, label: MEDIA_TYPE_LABELS[value] }));

// 像网盘里的原始文件名：带视频后缀，或带 S01E02、1080p 这类标记
function looksLikeFileName(value: string): boolean {
  const text = value.trim();
  return /\.(mkv|mp4|ts|m2ts|avi|mov|wmv|flv|webm|rmvb|iso|mpe?g)$/i.test(text)
    || /(^|[^a-z0-9])(S\d{1,2}E\d{1,4}|2160p|1080p|720p)([^a-z0-9]|$)/i.test(text);
}

function numbers(value: string): number[] {
  return value.split(/[，,\s]+/).map(Number).filter((item) => Number.isInteger(item) && item > 0);
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

/** 一次预览请求（preview_plan 的参数）；n 让同样的表单也能再生成一次。 */
interface PlanRequest {
  n: number;
  parsed: Record<string, unknown>;
  landed: Array<{ id: string; name: string }>;
  naming: { movie?: string; tv?: string };
}

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
  // 识别结果连同它对应的文件名一起存：文件名变了，旧结果自然失效
  const [recognition, setRecognition] = useState<{ names: string; result: Recognized | null } | null>(null);
  const [pending, setPending] = useState('');
  const [autoRun, setAutoRun] = useState({ requested: 0, handled: 0 });
  const [request, setRequest] = useState<PlanRequest | null>(null);
  // 媒体库整理插件的动作 preview_plan：按正在编辑的命名模板算出整理后的位置，不碰网盘
  const plan = useAsync(() => {
    if (!request) return Promise.resolve(null);
    const { n: _n, ...payload } = request;
    return configApi.extensionAction<{ items: IOrganizePreviewItem[] }>('organizer', 'preview_plan', payload);
  }, [request], '无法生成整理方案');
  const items = plan.data?.items ?? null;
  const busy = request !== null && plan.loading;
  const error = busy ? '' : plan.error;
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
      configApi.extensionAction<Recognized>('organizer', 'parse_names', { names: names.split('\n') }).then((g) => {
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
        setAutoRun((run) => ({ ...run, requested: run.requested + 1 }));
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

  const buildRequest = (): PlanRequest => {
    const tv = kind === 'tv';
    return {
      n: (request?.n ?? 0) + 1,
      parsed: {
        is_media: true,
        media_type: kind,
        title: title.trim(),
        year: Number(year) || null,
        season: tv ? Number(season) || null : null,
        episodes: tv ? numbers(episodes) : [],
        raw_fields: tmdbid ? { tmdbid } : {},
      },
      landed,
      naming,
    };
  };
  const preview = () => { if (ready && !busy) setRequest(buildRequest()); };

  // 识别完成、表单填好后自动跑一次；之后改了表单由用户点「生成预览」。在渲染时处理，表单已是识别后的值
  if (autoRun.requested !== autoRun.handled) {
    setAutoRun({ ...autoRun, handled: autoRun.requested });
    if (ready) setRequest(buildRequest());
  }

  // 窄屏下结果在表单下方，生成后自动滚过去。
  useEffect(() => {
    if (!plan.data || !window.matchMedia('(max-width: 1023px)').matches) return;
    const frame = requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    return () => cancelAnimationFrame(frame);
  }, [plan.data]);

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
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); preview(); }}>
            <Field
              label="转存后的文件名"
              hint="每行一个，就是网盘里转存下来的原始文件名；贴进来后自动识别片名与季集并生成预览。"
              extra={<span className="text-xs tabular-nums text-muted-foreground">{landed.length} 个文件</span>}
            >
              <Textarea
                value={files}
                onChange={(e) => setFiles(e.target.value)}
                placeholder={'在这里粘贴文件名，每行一个，例如：\n[死刑将至].Death.is.Coming.2026.S01E25.2160p…mp4'}
                spellCheck={false}
                wrap="off"
                className="min-h-36 resize-y bg-card font-mono text-xs leading-6"
              />
            </Field>
            <div className="space-y-1.5">
              <p className="text-[13px] font-medium leading-5">资源类型</p>
              <Segmented<Kind> label="资源类型" size="md" fill value={kind} options={KINDS}
                onChange={(value) => { touched.current.add('kind'); setKind(value); }} />
            </div>
            <Field
              label="片名"
              extra={recognizing
                ? <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><LoaderCircle className="size-3 animate-spin" />识别中…</span>
                : null}
              hint={recognized && !recognizing
                ? <>{SOURCE_NOTE[recognized.tmdb]}{tmdbid ? <span className="font-mono"> · tmdbid {tmdbid}</span> : null}</>
                : null}
            >
              <Input className="bg-card" autoComplete="off" value={title} onChange={(e) => changeTitle(e.target.value)} placeholder="整理后的片名，如：死刑将至" />
            </Field>
            <div className={cn('grid gap-3', kind === 'tv' && 'grid-cols-2')}>
              <Field label="年份">
                <Input className="bg-card" inputMode="numeric" value={year} onChange={(e) => edit('year', setYear)(e.target.value.replace(/\D/g, ''))} autoComplete="off" placeholder="选填" />
              </Field>
              {kind === 'tv' ? (
                <Field label="第几季">
                  <Input className="bg-card" inputMode="numeric" value={season} onChange={(e) => edit('season', setSeason)(e.target.value.replace(/\D/g, ''))} autoComplete="off" placeholder="不填为 1" />
                </Field>
              ) : null}
            </div>
            {kind === 'tv' ? (
              <Field label="包含哪几集" hint="用逗号或空格分隔，例如 1, 2, 3。">
                <Input className="bg-card" value={episodes} autoComplete="off" onChange={(e) => edit('episodes', setEpisodes)(e.target.value)} placeholder="不填则按文件名识别" />
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
