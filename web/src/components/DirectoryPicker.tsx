import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, Folder, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { extras, type IDirItem } from '@/api/extras';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';

type Node = { id: string; name: string };

export default function DirectoryPicker({ open, initialCid = '0', initialName = '根目录', onOpenChange, onSelect }:
  { open: boolean; initialCid?: string; initialName?: string; onOpenChange: (open: boolean) => void; onSelect: (dir: Node) => void }) {
  const [items, setItems] = useState<IDirItem[]>([]);
  const [path, setPath] = useState<Node[]>([]);
  const [current, setCurrent] = useState<Node>({ id: initialCid, name: initialName || '已选目录' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [next, setNext] = useState<number | null>(null);
  const version = useRef(0);

  const load = useCallback(async (cid: string, target: Node[], offset = 0) => {
    const request = ++version.current;
    setBusy(true); setError('');
    try {
      const result = await extras.dirs(cid, offset);
      if (request !== version.current) return;
      const resolvedPath = result.path?.length ? result.path : target;
      setItems(old => offset ? [...old, ...result.items] : result.items);
      setNext(result.next_offset); setPath(resolvedPath);
      setCurrent(resolvedPath.at(-1) ?? { id: '0', name: '根目录' });
    } catch (e) {
      if (request === version.current) setError(e instanceof Error ? e.message : '目录查询失败');
    } finally { if (request === version.current) setBusy(false); }
  }, []);

  useEffect(() => {
    if (!open) return;
    const cid = initialCid || '0';
    const target = cid === '0' ? [] : [{ id: cid, name: initialName || '当前目录' }];
    const timer = window.setTimeout(() => void load(cid, target), 0);
    return () => window.clearTimeout(timer);
  }, [open, initialCid, initialName, load]);

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[85vh] max-w-2xl overflow-hidden">
      <DialogHeader><DialogTitle>选择 115 目录</DialogTitle><DialogDescription>逐级进入目录并选择当前位置；不会创建、移动或删除网盘内容。</DialogDescription></DialogHeader>
      <nav aria-label="目录路径" className="flex flex-wrap items-center gap-1 rounded-md bg-muted/40 px-3 py-2 text-sm">
        <button className="text-primary" disabled={busy} onClick={() => void load('0', [])}>根目录</button>
        {path.map((x, i) => <span key={`${x.id}-${i}`} className="flex items-center gap-1"><ChevronRight className="size-3 text-muted-foreground" /><button className="max-w-40 truncate text-primary" disabled={busy} onClick={() => void load(x.id, path.slice(0, i + 1))}>{x.name}</button></span>)}
      </nav>
      {error && <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}<Button className="ml-2" size="sm" variant="outline" onClick={() => void load(current.id, path)}>重试</Button></div>}
      <div className="max-h-[48vh] overflow-auto rounded-md border">
        {busy && <p className="p-5 text-sm text-muted-foreground">正在读取目录…</p>}
        {!busy && !error && !items.length && <p className="p-8 text-center text-sm text-muted-foreground">当前目录没有子目录</p>}
        <ul className="divide-y">{items.map(item => <li key={item.id}><button className="flex w-full items-center gap-3 p-3 text-left hover:bg-muted" disabled={busy} onClick={() => void load(item.id, [...path, item])}><Folder className="size-4 shrink-0 text-primary" /><span className="min-w-0 flex-1 truncate">{item.name}</span><ChevronRight className="size-4 text-muted-foreground" /></button></li>)}</ul>
        {next !== null && <Button className="m-3" size="sm" variant="outline" disabled={busy} onClick={() => void load(current.id, path, next)}><RefreshCw className="size-4" />加载更多</Button>}
      </div>
      <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button><Button disabled={busy || Boolean(error)} onClick={() => { onSelect(current); onOpenChange(false); toast.success(`已选择目录「${current.name}」`); }}>选择当前目录</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
