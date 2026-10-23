import { CircleAlert, FilePlay, FolderOpen, FolderRoot, FolderTree, LoaderCircle, Play, WandSparkles } from 'lucide-react';
import { useRef, useState, type ReactNode } from 'react';
import { api } from '@/api/client';
import { EmptyState } from '@/components/app/EmptyState';
import { Page } from '@/components/app/Page';
import { Panel } from '@/components/app/Panel';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import type { IOrganizePreviewItem } from './types';

type Kind = 'tv' | 'movie';

const KINDS: Array<{ value: Kind; label: string }> = [
  { value: 'tv', label: '剧集' },
  { value: 'movie', label: '电影' },
];

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

/* ---------------- 页面 ---------------- */

export default function OrganizePage() {
  const [kind, setKind] = useState<Kind>('tv');
  const [title, setTitle] = useState('');
  const [year, setYear] = useState('');
  const [season, setSeason] = useState('1');
  const [episodes, setEpisodes] = useState('1, 2');
  const [files, setFiles] = useState('S01E01.mkv\nS01E02.mkv');
  const [items, setItems] = useState<IOrganizePreviewItem[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);

  const landed = files.split('\n').map((name) => name.trim()).filter(Boolean)
    .map((name, index) => ({ id: `preview-${index + 1}`, name }));
  const ready = Boolean(title.trim()) && landed.length > 0;

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
      };
      // 媒体库整理插件的动作 preview_plan：按当前命名模板算出整理后的位置，不碰网盘
      const result = await api.extensionAction<{ items: IOrganizePreviewItem[] }>('organizer', 'preview_plan', { parsed, landed });
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

  return (
    <Page
      title="命名预览"
      description="填写资源信息和转存后的文件名，看看按当前命名规则会整理成什么样。只是预览，不会改动网盘。"
    >
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <Panel title="资源信息" icon={WandSparkles} description="填得越准确，预览越接近真实整理效果">
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void preview(); }}>
            <Field label="资源类型">
              <div role="radiogroup" aria-label="资源类型" className="grid grid-cols-2 rounded-lg bg-muted p-0.5">
                {KINDS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={kind === option.value}
                    onClick={() => setKind(option.value)}
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
            <Field label="片名" htmlFor="organize-title">
              <Input id="organize-title" className="bg-card" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：示例剧名" />
            </Field>
            <div className={cn('grid gap-3', kind === 'tv' && 'grid-cols-2')}>
              <Field label="年份" htmlFor="organize-year">
                <Input id="organize-year" className="bg-card" inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, ''))} placeholder="2026" />
              </Field>
              {kind === 'tv' ? (
                <Field label="第几季" htmlFor="organize-season">
                  <Input id="organize-season" className="bg-card" inputMode="numeric" value={season} onChange={(e) => setSeason(e.target.value.replace(/\D/g, ''))} />
                </Field>
              ) : null}
            </div>
            {kind === 'tv' ? (
              <Field label="包含哪几集" htmlFor="organize-episodes" hint="用逗号或空格分隔，例如 1, 2, 3。">
                <Input id="organize-episodes" className="bg-card" value={episodes} onChange={(e) => setEpisodes(e.target.value)} placeholder="1, 2, 3" />
              </Field>
            ) : null}
            <Field
              label="转存后的文件名"
              htmlFor="organize-files"
              hint="每行一个，就是网盘里转存下来的原始文件名。"
              extra={<span className="text-xs tabular-nums text-muted-foreground">{landed.length} 个文件</span>}
            >
              <Textarea
                id="organize-files"
                value={files}
                onChange={(e) => setFiles(e.target.value)}
                spellCheck={false}
                wrap="off"
                className="min-h-36 resize-y bg-card font-mono text-xs leading-6"
              />
            </Field>
            <Button type="submit" className="h-10 w-full" disabled={busy || !ready}>
              {busy ? <LoaderCircle className="animate-spin" /> : <Play />}
              {busy ? '正在生成…' : '生成预览'}
            </Button>
            {!ready ? <p className="-mt-1 text-center text-xs text-muted-foreground">填写片名并至少写一个文件名后即可预览</p> : null}
          </form>
        </Panel>

        <div ref={resultRef} className="scroll-mt-20">
          <Panel title="整理后的结构" icon={FolderTree} description="使用当前命名规则，符合 Jellyfin / Emby 刮削规范">
            {error ? (
              <EmptyState icon={CircleAlert} title="无法生成整理方案" description={error} className="border-none py-16" />
            ) : items === null ? (
              <EmptyState
                icon={FolderTree}
                title="填好资源信息后生成预览"
                description="会列出整理后的文件夹层级和每个文件的新名字；不会执行任何转存、移动或改名。"
                className="border-none py-16"
              />
            ) : items.length === 0 ? (
              <EmptyState icon={FolderTree} title="没有需要整理的文件" description="命名规则没有为这些文件生成新位置，请检查文件名或资源类型。" className="border-none py-16" />
            ) : (
              <div className={cn('transition-opacity', busy && 'opacity-50')}>
                <PlanTree items={items} />
              </div>
            )}
          </Panel>
        </div>
      </div>
    </Page>
  );
}
