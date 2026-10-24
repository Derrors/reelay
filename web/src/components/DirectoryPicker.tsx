import { ChevronRight, CircleAlert, Folder, FolderOpen, House, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { useDirListing, type Crumb } from '@/hooks/useDirListing';
import { cn } from '@/lib/utils';

/** 选中的目录；path 是从根目录起的路径，如「影视 / 剧集」。 */
export type PickedDir = Crumb & { path: string };

/** 选择 115 目录的弹窗；打开时才挂载（见 BasicTab），从 initialCid 开始浏览。 */
export default function DirectoryPicker({ open, initialCid = '0', initialName = '根目录', onOpenChange, onSelect }:
  { open: boolean; initialCid?: string; initialName?: string; onOpenChange: (open: boolean) => void; onSelect: (dir: PickedDir) => void }) {
  const dir = useDirListing({
    cid: initialCid || '0',
    path: !initialCid || initialCid === '0' ? [] : [{ id: initialCid, name: initialName || '当前目录' }],
  });
  const current = dir.path.at(-1) ?? { id: '0', name: '根目录' };
  const navigating = dir.loading && !dir.ready;
  const busy = dir.loading || dir.loadingMore;
  const error = dir.loading ? '' : dir.error;
  const crumb = 'max-w-40 truncate rounded px-1.5 py-0.5 transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none max-sm:py-2';

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="flex max-h-[85vh] max-w-xl flex-col gap-0 overflow-hidden p-0">
      <DialogHeader className="space-y-1 px-5 pb-3 pr-12 pt-5 text-left">
        <DialogTitle className="text-base">选择 115 目录</DialogTitle>
        <DialogDescription className="text-xs">逐级进入目录后选择当前位置；不会创建、移动或删除网盘内容。</DialogDescription>
      </DialogHeader>

      <nav aria-label="目录路径" className="flex flex-wrap items-center gap-0.5 border-y border-border/70 bg-muted/40 px-4 py-2 text-[13px] text-muted-foreground">
        <button type="button" className={cn(crumb, 'flex items-center gap-1', !dir.path.length && 'font-medium text-foreground')} disabled={navigating} onClick={() => dir.open('0', [])}>
          <House className="size-3.5" />根目录
        </button>
        {dir.path.map((x, i) => (
          <span key={`${x.id}-${i}`} className="flex min-w-0 items-center gap-0.5">
            <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/60" />
            <button
              type="button" disabled={navigating} onClick={() => dir.open(x.id, dir.path.slice(0, i + 1))}
              className={cn(crumb, i === dir.path.length - 1 && 'font-medium text-foreground')}
            >{x.name}</button>
          </span>
        ))}
      </nav>

      <div className="min-h-56 flex-1 overflow-y-auto">
        {error ? (
          <div role="alert" className="m-4 flex flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3.5 py-3 text-sm text-destructive">
            <CircleAlert className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">{error}</span>
            <Button size="sm" variant="outline" className="bg-card" onClick={dir.reload}>重试</Button>
          </div>
        ) : !dir.ready ? (
          <div className="space-y-1 p-2" role="status" aria-label="正在读取目录">
            {[0, 1, 2, 3, 4].map(i => <Skeleton key={i} className="h-10 rounded-md" />)}
          </div>
        ) : !dir.items.length ? (
          <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
            <span className="grid size-10 place-items-center rounded-full bg-muted text-muted-foreground"><FolderOpen className="size-5" /></span>
            <p className="mt-3 text-sm font-medium">这里没有子目录</p>
            <p className="mt-1 text-xs text-muted-foreground">可以直接选择当前目录。</p>
          </div>
        ) : (
          <ul className={cn('p-2 transition-opacity', dir.loading && 'opacity-60')}>
            {dir.items.map(item => (
              <li key={item.id}>
                <button
                  type="button" disabled={busy} onClick={() => dir.open(item.id, [...dir.path, item])}
                  className="flex min-h-10 w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-muted disabled:opacity-60"
                >
                  <Folder className="size-4 shrink-0 text-primary" />
                  <span className="min-w-0 flex-1 truncate">{item.name}</span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                </button>
              </li>
            ))}
            {dir.next !== null ? (
              <li className="p-1">
                <Button className="w-full" size="sm" variant="ghost" disabled={busy} onClick={() => void dir.loadMore()}>
                  {dir.loadingMore ? <Loader2 className="animate-spin" /> : null}加载更多
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
            className="flex-1 sm:flex-none" disabled={busy || Boolean(error)}
            onClick={() => {
              onSelect({ ...current, path: dir.path.map(x => x.name).join(' / ') || '根目录' });
              onOpenChange(false);
              toast.success(`已选择目录「${current.name}」`);
            }}
          >选择当前目录</Button>
        </div>
      </div>
    </DialogContent>
  </Dialog>;
}
