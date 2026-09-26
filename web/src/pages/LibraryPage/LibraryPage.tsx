import { useEffect, useState } from 'react';
import { Search, Tv, Film, HardDrive } from 'lucide-react';
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
      <PageHeader eyebrow="媒体库" title="已转存片名聚合" description="按片名/剧集聚合成媒体库视图，显示收录集数与体积，支持检索。" />
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <Card className="rounded-md shadow-none sm:col-span-2">
          <CardContent className="flex items-center gap-3 pt-6">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input className="pl-9" placeholder="搜索片名…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <NativeSelect className="w-32" value={mt} onChange={(e) => setMt(e.target.value)}>
              <NativeSelectOption value="">全部</NativeSelectOption>
              <NativeSelectOption value="tv">剧集</NativeSelectOption>
              <NativeSelectOption value="movie">电影</NativeSelectOption>
            </NativeSelect>
          </CardContent>
        </Card>
        <Card className="rounded-md shadow-none"><CardContent className="pt-6">
          <div className="text-xs text-muted-foreground">已完成估算体积</div>
          <div className="mt-1 flex items-center gap-2 font-mono text-2xl font-semibold text-primary">
            <HardDrive className="size-5" />{gb} GB
          </div>
        </CardContent></Card>
      </div>
      <Card className="mt-4 rounded-md shadow-none">
        <CardContent className="p-2">
          {groups === null ? <Skeleton className="m-2 h-80" />
            : groups.length === 0 ? <p className="p-10 text-center text-sm text-muted-foreground">没有匹配的已完成资源</p>
            : (
              <ul className="divide-y">
                {groups.map((g) => (
                  <li key={g.title} className="grid grid-cols-[auto_1fr_auto] items-center gap-4 px-3 py-3">
                    <span className={`flex size-8 items-center justify-center rounded-md ${g.media_type === 'tv' ? 'bg-primary/10 text-primary' : 'bg-amber-500/10 text-amber-600'}`}>
                      {g.media_type === 'tv' ? <Tv className="size-4" /> : <Film className="size-4" />}
                    </span>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{g.title} {g.year ? <span className="text-muted-foreground">({g.year})</span> : null}</div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {g.media_type === 'tv' ? `收录 ${g.episodes.length} 集：${epsText(g.episodes)}` : `${g.tasks} 个文件`}
                      </div>
                    </div>
                    <span className="font-mono text-xs text-muted-foreground">{fmtGb(g.bytes)} GB</span>
                  </li>
                ))}
              </ul>
            )}
        </CardContent>
      </Card>
    </div>
  );
}
