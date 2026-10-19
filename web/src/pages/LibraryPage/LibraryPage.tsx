import { useEffect, useState } from 'react';
import { Search, Tv, Film, HardDrive, Library } from 'lucide-react';
import PageHeader from '@/components/PageHeader';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Skeleton } from '@/components/ui/skeleton';
import { extras, type ITitleGroup } from '@/api/extras';

function fmtGb(b: number): string {
  return (b / 1024 ** 3).toFixed(2);
}
function epsText(eps: number[]): string {
  if (!eps.length) return '—';
  // 折叠连续集数 1-3,5
  const out: string[] = [];
  let start = eps[0]; let prev = eps[0];
  const flush = () => out.push(start === prev ? `${start}` : `${start}-${prev}`);
  for (let i = 1; i < eps.length; i += 1) {
    if (eps[i] === prev + 1) { prev = eps[i]; continue; }
    flush(); start = prev = eps[i];
  }
  flush();
  return out.join(', ');
}

export default function LibraryPage() {
  const [q, setQ] = useState('');
  const [mt, setMt] = useState('');
  const [groups, setGroups] = useState<ITitleGroup[] | null>(null);
  const [gb, setGb] = useState(0);

  const load = () => {
    setGroups(null);
    extras.advancedTasks(q || undefined, mt || undefined)
      .then((r) => { setGroups(r.groups); setGb(r.estimated_gb); })
      .catch(() => setGroups([]));
  };

  useEffect(() => {
    const t = window.setTimeout(load, 300); // 输入防抖
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, mt]);

  return (
    <div className="mx-auto w-full max-w-5xl p-5 sm:p-8">
      <PageHeader eyebrow="媒体库" title="已转存的影视作品" description="按片名把已入库的资源聚合成媒体库视图，可以快速查看收录了哪些剧集、各占多少空间。" />
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <Card className="rounded-xl border-border/70 shadow-sm sm:col-span-2">
          <CardContent className="flex items-center gap-3 p-5">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input className="h-10 rounded-lg pl-9" placeholder="搜索片名…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <NativeSelect className="h-10 w-32 rounded-lg" value={mt} onChange={(e) => setMt(e.target.value)}>
              <NativeSelectOption value="">全部类型</NativeSelectOption>
              <NativeSelectOption value="tv">剧集</NativeSelectOption>
              <NativeSelectOption value="movie">电影</NativeSelectOption>
            </NativeSelect>
          </CardContent>
        </Card>
        <Card className="rounded-xl border-border/70 shadow-sm"><CardContent className="p-5">
          <div className="flex items-center justify-between">
            <div className="text-xs text-muted-foreground">已入库估算体积</div>
            <HardDrive className="size-4 text-primary" />
          </div>
          <div className="mt-2 text-2xl font-semibold tracking-tight text-primary">{gb} <span className="text-sm font-normal text-muted-foreground">GB</span></div>
        </CardContent></Card>
      </div>
      <Card className="mt-4 overflow-hidden rounded-xl border-border/70 shadow-sm">
        <CardContent className="p-2">
          {groups === null ? <Skeleton className="m-2 h-96" />
            : groups.length === 0 ? (
              <div className="p-14 text-center">
                <div className="mx-auto grid size-12 place-items-center rounded-full bg-muted text-muted-foreground"><Library className="size-6" /></div>
                <p className="mt-3 text-sm font-medium">还没有已入库的资源</p>
                <p className="mt-1 text-xs text-muted-foreground">完成转存的影视作品会自动汇总到这里</p>
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
                {groups.map((g) => (
                  <li key={g.title} className="grid grid-cols-[auto_1fr_auto] items-center gap-4 rounded-lg px-3 py-3 transition-colors hover:bg-muted/50">
                    <span className={`flex size-9 items-center justify-center rounded-xl ${g.media_type === 'tv' ? 'bg-primary/10 text-primary' : 'bg-amber-500/10 text-amber-600'}`}>
                      {g.media_type === 'tv' ? <Tv className="size-[18px]" /> : <Film className="size-[18px]" />}
                    </span>
                    <div className="min-w-0">
                      <div className="truncate text-[13.5px] font-medium">{g.title} {g.year ? <span className="font-normal text-muted-foreground">({g.year})</span> : null}</div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {g.media_type === 'tv' ? `已收录 ${g.episodes.length} 集：${epsText(g.episodes)}` : `${g.tasks} 个文件`}
                      </div>
                    </div>
                    <span className="text-xs tabular-nums text-muted-foreground">{fmtGb(g.bytes)} GB</span>
                  </li>
                ))}
              </ul>
            )}
        </CardContent>
      </Card>
    </div>
  );
}
