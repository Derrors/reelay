import { useCallback, useEffect, useMemo, useState } from 'react';
import { Pause, Play, RefreshCw, Search, TerminalSquare } from 'lucide-react';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { extras } from '@/api/extras';

export default function LogsPage() {
  const [lines, setLines] = useState<string[] | null>(null);
  const [q, setQ] = useState('');
  const [auto, setAuto] = useState(true);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await extras.logs(600, q || undefined);
      setLines(r.exists ? r.lines : []);
      setErr(r.exists ? '' : '后台服务还没有产生日志文件；服务运行并处理消息后，这里会自动出现日志。');
    } catch (e) {
      setErr(e instanceof Error ? e.message : '读取日志失败');
    }
  }, [q]);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    if (!auto) return () => window.clearTimeout(initial);
    const t = window.setInterval(() => void load(), 5000);
    return () => { window.clearTimeout(initial); window.clearInterval(t); };
  }, [auto, load]);

  const shown = useMemo(() => (lines ?? []).slice(-300), [lines]);

  return (
    <div className="mx-auto w-full max-w-6xl p-5 sm:p-8">
      <PageHeader
        eyebrow="运行日志"
        title="后台实时日志"
        description="监听、识别、转存和通知的详细运行记录都在这里，方便排查问题。默认每 5 秒自动滚动到最新。"
        action={
          <div className="flex gap-2">
            <Button variant={auto ? 'default' : 'outline'} size="sm" className="rounded-lg" onClick={() => setAuto((v) => !v)}>
              {auto ? <><Pause className="size-3.5" />暂停自动刷新</> : <><Play className="size-3.5" />开启自动刷新</>}
            </Button>
            <Button variant="outline" size="sm" className="rounded-lg" onClick={() => void load()}><RefreshCw className="size-3.5" />立即刷新</Button>
          </div>
        }
      />
      <Card className="mt-6 overflow-hidden rounded-xl border-border/70 shadow-sm">
        <CardContent className="p-4">
          <div className="relative mb-3">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="h-10 rounded-lg pl-9" placeholder="输入关键字过滤，如：失败、115、审批" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          {err ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
              <TerminalSquare className="size-8 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">{err}</p>
            </div>
          ) : lines === null ? <Skeleton className="h-[60vh]" /> : (
            <pre className="h-[60vh] overflow-auto rounded-lg bg-[#102322] p-4 font-mono text-xs leading-5 text-[#b9d6d0]">
              {shown.length ? shown.join('\n') : '（没有匹配的日志）'}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
