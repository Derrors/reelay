import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, Search } from 'lucide-react';
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
      setErr(r.exists ? '' : '尚未产生日志文件（worker 未配置 REELAY_LOG_FILE）');
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
        title="Worker 实时日志"
        description="查看监听器、解析、转存与通知的运行日志，支持关键字过滤与 5 秒自动刷新。"
        action={
          <div className="flex gap-2">
            <Button variant={auto ? 'default' : 'outline'} size="sm" onClick={() => setAuto((v) => !v)}>
              {auto ? '自动刷新中' : '已暂停'}
            </Button>
            <Button variant="outline" size="sm" onClick={() => void load()}><RefreshCw className="size-4" />刷新</Button>
          </div>
        }
      />
      <Card className="mt-6 rounded-md shadow-none">
        <CardContent className="p-4">
          <div className="relative mb-3">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="pl-9" placeholder="过滤关键字（如 failed / 115 / 审批）" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          {err ? <p className="py-8 text-center text-sm text-muted-foreground">{err}</p>
            : lines === null ? <Skeleton className="h-96" />
            : (
              <pre className="h-[60vh] overflow-auto rounded bg-muted/40 p-3 font-mono text-xs leading-5">
                {shown.length ? shown.join('\n') : '（无匹配日志）'}
              </pre>
            )}
        </CardContent>
      </Card>
    </div>
  );
}
