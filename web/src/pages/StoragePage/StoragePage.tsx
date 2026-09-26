import { useEffect, useState } from 'react';
import { Folder, HardDrive, RefreshCw } from 'lucide-react';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import { extras, type IDirItem } from '@/api/extras';

export default function StoragePage() {
  const [items, setItems] = useState<IDirItem[] | null>(null);
  const [path, setPath] = useState<Array<{ id: number; name: string }>>([]);
  const [err, setErr] = useState('');

  const load = async (cid: number) => {
    setItems(null);
    setErr('');
    try {
      const r = await extras.dirs(cid);
      setItems(r.items);
    } catch (e) {
      setErr(e instanceof Error ? e.message : '读取 115 目录失败');
    }
  };

  useEffect(() => { void load(0); }, []);

  const open = (it: IDirItem) => {
    setPath((p) => [...p, { id: it.id, name: it.name }]);
    void load(it.id);
  };
  const goRoot = () => { setPath([]); void load(0); };
  const goTo = (idx: number) => {
    const np = path.slice(0, idx + 1);
    setPath(np);
    void load(np.length ? np[np.length - 1].id : 0);
  };

  return (
    <div className="mx-auto w-full max-w-4xl p-5 sm:p-8">
      <PageHeader
        eyebrow="115 网盘"
        title="目标目录浏览"
        description="只读浏览你的 115 目录树，用于核对 save_dirs 与转存落点。"
        action={<Button variant="outline" size="sm" onClick={goRoot}><RefreshCw className="size-4" />回根目录</Button>}
      />
      <div className="mt-4 flex flex-wrap items-center gap-1 text-sm">
        <button className="rounded px-2 py-1 text-primary hover:bg-muted" onClick={goRoot}>根目录</button>
        {path.map((seg, i) => (
          <span key={seg.id} className="flex items-center gap-1">
            <span className="text-muted-foreground">/</span>
            <button className="rounded px-2 py-1 text-primary hover:bg-muted" onClick={() => goTo(i)}>{seg.name}</button>
          </span>
        ))}
      </div>
      <Card className="mt-3 rounded-md shadow-none">
        <CardContent className="p-2">
          {err ? <p className="p-6 text-center text-sm text-destructive">{err}</p>
            : items === null ? <Skeleton className="m-3 h-80" />
            : items.length === 0 ? <p className="p-8 text-center text-sm text-muted-foreground">空目录</p>
            : (
              <ul className="divide-y">
                {items.map((it) => (
                  <li key={it.id}>
                    <button className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm hover:bg-muted/60" onClick={() => open(it)}>
                      <Folder className="size-4 text-primary" />
                      <span className="flex-1 truncate">{it.name}</span>
                      <span className="font-mono text-xs text-muted-foreground">{it.id}</span>
                      <HardDrive className="size-3 text-muted-foreground/50" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
        </CardContent>
      </Card>
    </div>
  );
}
