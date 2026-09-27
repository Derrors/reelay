import { useEffect, useRef, useState } from 'react';
import { Folder, RefreshCw, Copy, ArrowRight } from 'lucide-react';
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
  const [via, setVia] = useState('');
  const version = useRef(0);
  const current = path.at(-1)?.id ?? '0';
  const load = async (cid: string, target: typeof path, offset = 0) => {
    const request = ++version.current;
    setBusy(true); setError('');
    try {
      const result = await extras.dirs(cid, offset);
      if (request !== version.current) return;
      setItems(old => offset ? [...old, ...result.items] : result.items);
      setNext(result.next_offset); setVia(result.via); setPath(target);
    } catch (e) {
      if (request === version.current) setError(e instanceof Error ? e.message : '目录查询失败');
    } finally { if (request === version.current) setBusy(false); }
  };
  useEffect(() => {
    let active = true;
    extras.dirs('0').then(r => { if (active) { setItems(r.items); setNext(r.next_offset); setVia(r.via); } })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : '目录查询失败'); });
    return () => { active = false; };
  }, []);
  return <div className="mx-auto w-full max-w-5xl p-5 sm:p-8">
    <PageHeader eyebrow="115 网盘" title="目标目录浏览" description="只读浏览目录，复制CID后可用于目录映射。所有目录ID按字符串传输，避免大整数精度丢失。"
      action={<Button variant="outline" disabled={busy} onClick={() => void load(current, path)}><RefreshCw className="size-4" />刷新当前目录</Button>} />
    <div className="my-5 flex flex-wrap gap-2">
      <Input aria-label="目录CID" value={manualCid} onChange={e => setManualCid(e.target.value)} className="max-w-sm font-mono" placeholder="输入目录CID直接定位" />
      <Button variant="outline" disabled={busy} onClick={() => { if (!/^[0-9]+$/.test(manualCid)) { toast.error('CID必须是非负整数'); return; } void load(manualCid, manualCid === '0' ? [] : [{ id: manualCid, name: '指定目录' }]); }}><ArrowRight className="size-4" />定位</Button>
      <Link to="/config" className="px-3 py-2 text-sm text-primary underline">配置目录映射</Link>
    </div>
    <nav aria-label="目录路径" className="my-4 flex flex-wrap items-center gap-2 text-sm">
      <button className="text-primary" disabled={busy} onClick={() => void load('0', [])}>根目录</button>
      {path.map((x, i) => <span key={x.id}> / <button disabled={busy} className="text-primary" onClick={() => void load(x.id, path.slice(0, i + 1))}>{x.name}</button></span>)}
    </nav>
    {error && <div role="alert" className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-5"><p className="text-sm text-destructive">{error}</p><div className="mt-3 flex gap-4"><Button variant="outline" size="sm" onClick={() => void load(current, path)}>重新查询</Button><Link to="/config?tab=connections" className="text-sm text-primary underline">检查115连接状态</Link></div></div>}
    <Card className="shadow-none"><CardContent className="p-3">
      {busy && <p role="status" className="p-4 text-sm text-muted-foreground">正在读取目录…</p>}
      {!busy && !error && items.length === 0 && <p className="p-8 text-center text-muted-foreground">本页没有子目录{next !== null ? '，可继续加载后续条目。' : '。'}</p>}
      <ul className="divide-y">{items.map(x => <li key={x.id} className="flex items-center gap-2">
        <button disabled={busy} className="flex min-w-0 flex-1 items-center gap-3 p-3 text-left hover:bg-muted" onClick={() => void load(x.id, [...path, x])}><Folder className="size-4 shrink-0 text-primary" /><span className="flex-1 truncate">{x.name}</span><span className="font-mono text-xs text-muted-foreground">{x.id}</span></button>
        <Button aria-label={`复制${x.name}的CID`} variant="ghost" size="icon" onClick={() => void navigator.clipboard.writeText(x.id).then(() => toast.success('CID已复制')).catch(() => toast.error('无法访问剪贴板，请手动复制CID'))}><Copy className="size-4" /></Button>
      </li>)}</ul>
      {next !== null && <Button className="mt-4" variant="outline" disabled={busy} onClick={() => void load(current, path, next)}>加载更多</Button>}
    </CardContent></Card><p className="mt-3 text-xs text-muted-foreground">当前CID：{current}{via ? ` · 查询通道：${via}` : ''} · 不执行网盘写操作</p>
  </div>;
}
