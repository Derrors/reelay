import { useEffect, useRef, useState } from 'react';
import { ChevronRight, Folder, RefreshCw, Copy, ArrowRight, Search } from 'lucide-react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { extras, type IDirItem } from '@/api/extras';

export default function StoragePage() {
  const [items, setItems] = useState<IDirItem[]>([]);
  const [path, setPath] = useState<Array<{ id: string; name: string }>>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [next, setNext] = useState<number | null>(null);
  const [manualCid, setManualCid] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const version = useRef(0);
  const current = path.at(-1)?.id ?? '0';
  const load = async (cid: string, target: typeof path, offset = 0) => {
    const request = ++version.current;
    setBusy(true); setError('');
    try {
      const result = await extras.dirs(cid, offset);
      if (request !== version.current) return;
      setItems(old => offset ? [...old, ...result.items] : result.items);
      setNext(result.next_offset); setPath(target);
    } catch (e) {
      if (request === version.current) setError(e instanceof Error ? e.message : '目录查询失败');
    } finally { if (request === version.current) setBusy(false); }
  };
  useEffect(() => {
    let active = true;
    extras.dirs('0').then(r => { if (active) { setItems(r.items); setNext(r.next_offset); } })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : '目录查询失败'); });
    return () => { active = false; };
  }, []);
  return <div className="mx-auto w-full max-w-5xl p-5 sm:p-8">
    <PageHeader eyebrow="115 网盘" title="浏览网盘目录"
      description="只读浏览你的 115 网盘，确认保存位置是否正确。这里不会创建、移动或删除任何文件。"
      action={<Button variant="outline" className="rounded-lg" disabled={busy} onClick={() => void load(current, path)}><RefreshCw className="size-4" />刷新</Button>} />

    <nav aria-label="当前位置" className="mt-5 flex flex-wrap items-center gap-1 rounded-xl border border-border/70 bg-card px-4 py-2.5 text-sm shadow-sm">
      <button className="flex items-center gap-1 font-medium text-primary" disabled={busy} onClick={() => void load('0', [])}><Folder className="size-4" />全部文件</button>
      {path.map((x, i) => <span key={x.id} className="flex items-center gap-1"><ChevronRight className="size-3.5 text-muted-foreground" /><button disabled={busy} className="max-w-52 truncate font-medium text-primary" onClick={() => void load(x.id, path.slice(0, i + 1))}>{x.name}</button></span>)}
    </nav>

    {error && <div role="alert" className="mt-4 rounded-xl border border-destructive/30 bg-destructive/5 p-5"><p className="text-sm text-destructive">{error}</p><div className="mt-3 flex gap-4"><Button variant="outline" size="sm" onClick={() => void load(current, path)}>重新查询</Button><Link to="/config?tab=connections" className="text-sm text-primary underline">检查 115 连接</Link></div></div>}

    <Card className="mt-4 rounded-xl border-border/70 shadow-sm">
      <CardContent className="p-2">
        {busy && <p role="status" className="p-6 text-sm text-muted-foreground">正在读取目录…</p>}
        {!busy && !error && items.length === 0 && <p className="p-12 text-center text-sm text-muted-foreground">这个文件夹里没有子文件夹{next !== null ? '，可继续加载后面的内容。' : '。'}</p>}
        <ul className="divide-y divide-border/60">
          {items.map(x => <li key={x.id} className="group flex items-center gap-2 rounded-lg">
            <button disabled={busy} className="flex min-w-0 flex-1 items-center gap-3 rounded-lg p-3 text-left transition-colors hover:bg-muted/70" onClick={() => void load(x.id, [...path, x])}>
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/8 text-primary"><Folder className="size-4" /></span>
              <span className="flex-1 truncate text-[13.5px] font-medium">{x.name}</span>
              <span className="font-mono text-[11px] text-muted-foreground/50 opacity-0 transition group-hover:opacity-100">{x.id}</span>
            </button>
            <Button aria-label={`复制「${x.name}」的目录编号`} variant="ghost" size="icon" className="mr-1 opacity-0 transition group-hover:opacity-100" onClick={() => void navigator.clipboard.writeText(x.id).then(() => toast.success('目录编号已复制')).catch(() => toast.error('无法访问剪贴板，请手动复制'))}><Copy className="size-4" /></Button>
          </li>)}
        </ul>
        {next !== null && <Button className="m-3 rounded-lg" variant="outline" disabled={busy} onClick={() => void load(current, path, next)}>加载更多</Button>}
      </CardContent>
    </Card>

    <div className="mt-4">
      <button type="button" onClick={() => setShowAdvanced(v => !v)} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
        <Search className="size-3.5" />{showAdvanced ? '收起按编号定位' : '知道目录编号？直接定位'}
      </button>
      {showAdvanced && <div className="mt-2 flex flex-wrap gap-2">
        <Input aria-label="目录编号" value={manualCid} onChange={e => setManualCid(e.target.value)} className="max-w-sm font-mono" placeholder="输入目录编号后回车" onKeyDown={(e) => { if (e.key === 'Enter') { if (!/^[0-9]+$/.test(manualCid)) { toast.error('目录编号必须是纯数字'); return; } void load(manualCid, manualCid === '0' ? [] : [{ id: manualCid, name: '指定目录' }]); } }} />
        <Button variant="outline" disabled={busy} onClick={() => { if (!/^[0-9]+$/.test(manualCid)) { toast.error('目录编号必须是纯数字'); return; } void load(manualCid, manualCid === '0' ? [] : [{ id: manualCid, name: '指定目录' }]); }}><ArrowRight className="size-4" />定位</Button>
        <Link to="/config" className="flex items-center px-2 text-sm text-primary underline">去设置保存目录</Link>
      </div>}
      <p className="mt-2 text-xs text-muted-foreground">一般使用无需关心目录编号；它只在和外部工具对接时才需要。</p>
    </div>
  </div>;
}
