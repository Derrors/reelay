import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, CircleAlert, Folder, FolderOpen, House, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { extras, type IDirItem } from '@/api/extras';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

type Node = { id: string; name: string };

export default function DirectoryPicker({ open, initialCid = '0', initialName = '根目录', onOpenChange, onSelect }:
  { open: boolean; initialCid?: string; initialName?: string; onOpenChange: (open: boolean) => void; onSelect: (dir: Node) => void }) {
  const [items, setItems] = useState<IDirItem[]>([]);
  const [path, setPath] = useState<Node[]>([]);
  const [current, setCurrent] = useState<Node>({ id: initialCid, name: initialName || '已选目录' });
  const [busy, setBusy] = useState<'nav' | 'more' | null>(null);
  const [error, setError] = useState('');
  const [next, setNext] = useState<number | null>(null);
  const version = useRef(0);

  const load = useCallback(async (cid: string, target: Node[], offset = 0) => {
    const request = ++version.current;
    setBusy(offset ? 'more' : 'nav'); setError('');
    try {
      const result = await extras.dirs(cid, offset);
      if (request !== version.current) return;
      const resolvedPath = result.path?.length ? result.path : target;
      setItems(old => offset ? [...old, ...result.items] : result.items);
      setNext(result.next_offset); setPath(resolvedPath);
      setCurrent(resolvedPath.at(-1) ?? { id: '0', name: '根目录' });
    } catch (e) {
      if (request === version.current) setError(e instanceof Error ? e.message : '目录查询失败');
    } finally { if (request === version.current) setBusy(null); }
  }, []);

  useEffect(() => {
    if (!open) return;
    const cid = initialCid || '0';
    const target = cid === '0' ? [] : [{ id: cid, name: initialName || '当前目录' }];
    const timer = window.setTimeout(() => void load(cid, target), 0);
    return () => window.clearTimeout(timer);
  }, [open, initialCid, initialName, load]);

  const navigating = busy === 'nav';
  const crumb = 'max-w-40 truncate rounded px-1.5 py-0.5 transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none';

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="flex max-h-[85vh] max-w-xl flex-col gap-0 overflow-hidden p-0">
      <DialogHeader className="space-y-1 px-5 pb-3 pr-12 pt-5 text-left">
        <DialogTitle className="text-base">选择 115 目录</DialogTitle>
        <DialogDescription className="text-xs">逐级进入目录后选择当前位置；不会创建、移动或删除网盘内容。</DialogDescription>
      </DialogHeader>

      <nav aria-label="目录路径" className="flex flex-wrap items-center gap-0.5 border-y border-border/70 bg-muted/40 px-4 py-2 text-[13px] text-muted-foreground">
        <button type="button" className={cn(crumb, 'flex items-center gap-1', !path.length && 'font-medium text-foreground')} disabled={navigating} onClick={() => void load('0', [])}>
          <House className="size-3.5" />根目录
        </button>
        {path.map((x, i) => (
          <span key={`${x.id}-${i}`} className="flex min-w-0 items-center gap-0.5">
            <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/60" />
            <button
              type="button" disabled={navigating} onClick={() => void load(x.id, path.slice(0, i + 1))}
              className={cn(crumb, i === path.length - 1 && 'font-medium text-foreground')}
            >{x.name}</button>
          </span>
        ))}
      </nav>

      <div className="min-h-56 flex-1 overflow-y-auto">
        {error ? (
          <div role="alert" className="m-4 flex flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3.5 py-3 text-sm text-destructive">
            <CircleAlert className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">{error}</span>
            <Button size="sm" variant="outline" className="bg-card" onClick={() => void load(current.id, path)}>重试</Button>
          </div>
        ) : navigating ? (
          <div className="space-y-1 p-2" role="status" aria-label="正在读取目录">
            {[0, 1, 2, 3, 4].map(i => <Skeleton key={i} className="h-10 rounded-md" />)}
          </div>
        ) : !items.length ? (
          <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
            <span className="grid size-10 place-items-center rounded-full bg-muted text-muted-foreground"><FolderOpen className="size-5" /></span>
            <p className="mt-3 text-sm font-medium">这里没有子目录</p>
            <p className="mt-1 text-xs text-muted-foreground">可以直接选择当前目录。</p>
          </div>
        ) : (
          <ul className="p-2">
            {items.map(item => (
              <li key={item.id}>
                <button
                  type="button" disabled={busy !== null} onClick={() => void load(item.id, [...path, item])}
                  className="flex min-h-10 w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-muted disabled:opacity-60"
                >
                  <Folder className="size-4 shrink-0 text-primary" />
                  <span className="min-w-0 flex-1 truncate">{item.name}</span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                </button>
              </li>
            ))}
            {next !== null ? (
              <li className="p-1">
                <Button className="w-full" size="sm" variant="ghost" disabled={busy !== null} onClick={() => void load(current.id, path, next)}>
                  {busy === 'more' ? <Loader2 className="animate-spin" /> : null}加载更多
                </Button>
              </li>
            ) : null}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-3 border-t border-border/70 bg-card/60 px-5 py-3 sm:flex-row sm:items-center">
        <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
          将选择：<span className="font-medium text-foreground">{current.id === '0' ? '根目录' : current.name}</span>
        </p>
        <div className="flex gap-2">
          <Button variant="outline" className="flex-1 sm:flex-none" onClick={() => onOpenChange(false)}>取消</Button>
          <Button
            className="flex-1 sm:flex-none" disabled={busy !== null || Boolean(error)}
            onClick={() => { onSelect(current); onOpenChange(false); toast.success(`已选择目录「${current.name}」`); }}
          >选择当前目录</Button>
        </div>
      </div>
    </DialogContent>
  </Dialog>;
}
