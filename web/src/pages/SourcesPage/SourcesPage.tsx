import { useEffect, useState } from 'react';
import { Play } from 'lucide-react';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { NativeSelect } from '@/components/ui/native-select';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import { extras, type ISourcesResp } from '@/api/extras';
import type { IParsedResource } from '@/lib/types';

const SAMPLE = '📺 示例剧名 (2026) S01E01 ✨4K WEB-DL\n💾 大小：1.2 GB\n🔗 链接： 点击跳转';

export default function SourcesPage() {
  const [data, setData] = useState<ISourcesResp | null>(null);
  const [source, setSource] = useState('');
  const [text, setText] = useState(SAMPLE);
  const [urls, setUrls] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ parsed: IParsedResource | null; decision: string } | null>(null);

  useEffect(() => {
    extras.sources().then((d) => {
      setData(d);
      setSource(String(d.sources[0]?.bot ?? ''));
    }).catch(() => undefined);
  }, []);

  const run = async () => {
    setBusy(true);
    try {
      const r = await extras.testParse(text, urls.split(/[\s,]+/).filter(Boolean), source || undefined);
      setResult(r);
      toast.success(`解析完成，决策：${r.decision}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '测试失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-4xl p-5 sm:p-8">
      <PageHeader eyebrow="监听源" title="规则与解析测试" description="不入库、不转存，立即用当前规则与 LLM 对一段消息做识别/决策演练。" />
      {!data ? <Skeleton className="mt-6 h-72" /> : (
        <div className="mt-6 grid gap-5 lg:grid-cols-2">
          <Card className="rounded-md shadow-none">
            <CardHeader><CardTitle className="text-base">输入</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <NativeSelect value={source} onChange={(e) => setSource(e.target.value)}>
                {data.sources.map((s) => <option key={String(s.bot)} value={String(s.bot)}>{String(s.bot)}</option>)}
              </NativeSelect>
              <Textarea className="min-h-48 font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} />
              <input className="w-full rounded-md border px-3 py-2 text-xs" placeholder="超链接 URL（逗号分隔，可选）" value={urls} onChange={(e) => setUrls(e.target.value)} />
              <Button onClick={run} disabled={busy}><Play className="size-4" />{busy ? '解析中…' : '立即解析测试'}</Button>
            </CardContent>
          </Card>
          <Card className="rounded-md shadow-none">
            <CardHeader><CardTitle className="text-base">结果</CardTitle></CardHeader>
            <CardContent>
              {!result ? <p className="text-sm text-muted-foreground">在左侧填入消息后点「立即解析测试」。</p>
                : (
                  <pre className="overflow-auto rounded bg-muted/40 p-3 text-xs leading-5">
{JSON.stringify(result, null, 2)}
                  </pre>
                )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
