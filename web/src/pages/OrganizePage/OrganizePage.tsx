import { useState } from 'react';
import { ArrowRight, Play } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import PageHeader from '@/components/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import type { IOrganizePreviewItem, IParsedResource } from '@/lib/types';

function numbers(value: string): number[] {
  return value.split(/[，,\s]+/).map(Number).filter((item) => Number.isInteger(item) && item > 0);
}

export default function OrganizePage() {
  const [parsed, setParsed] = useState<IParsedResource>({ media_type: 'tv', title: '', year: null, season: 1, episodes: [] });
  const [episodes, setEpisodes] = useState('1, 2');
  const [files, setFiles] = useState('S01E01.mkv\nS01E02.mkv');
  const [items, setItems] = useState<IOrganizePreviewItem[]>([]);
  const [busy, setBusy] = useState(false);

  const preview = async () => {
    const landed = files.split('\n').map((name) => name.trim()).filter(Boolean).map((name, index) => ({ id: `preview-${index + 1}`, name }));
    if (!parsed.title || landed.length === 0) {
      toast.error('请填写片名和至少一个文件名');
      return;
    }
    setBusy(true);
    try {
      const result = await api.organizePreview({ ...parsed, episodes: numbers(episodes), is_media: true }, landed);
      setItems(result.items);
      toast.success('整理计划已生成');
    } catch (err) {
      setItems([]);
      toast.error(err instanceof Error ? err.message : '无法生成整理计划');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader eyebrow="整理预览" title="先看目标路径，再执行改名移动" description="这里调用与实际 after_save Hook 相同的确定性命名逻辑。无法从文件名识别季集号时会直接报错，不按列表顺序猜测。" />

      <div className="mt-6 grid min-w-0 gap-6 xl:grid-cols-[380px_minmax(0,1fr)]">
        <Card className="rounded-md shadow-none"><CardHeader><CardTitle className="text-base">资源与文件</CardTitle><CardDescription>只生成计划，不写入 115。</CardDescription></CardHeader><CardContent className="space-y-4">
          <label className="text-xs text-muted-foreground">资源类型<NativeSelect value={parsed.media_type ?? 'tv'} onChange={(event) => setParsed({ ...parsed, media_type: event.target.value })} className="mt-1 w-full"><NativeSelectOption value="tv">剧集</NativeSelectOption><NativeSelectOption value="movie">电影</NativeSelectOption></NativeSelect></label>
          <label className="text-xs text-muted-foreground">片名<Input value={parsed.title ?? ''} onChange={(event) => setParsed({ ...parsed, title: event.target.value })} placeholder="例如：法医秦明之龙番往事" className="mt-1" /></label>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-muted-foreground">年份<Input type="number" value={parsed.year ?? ''} onChange={(event) => setParsed({ ...parsed, year: Number(event.target.value) || null })} className="mt-1" /></label>
            <label className="text-xs text-muted-foreground">季<Input type="number" value={parsed.season ?? ''} onChange={(event) => setParsed({ ...parsed, season: Number(event.target.value) || null })} className="mt-1" /></label>
          </div>
          {parsed.media_type === 'tv' ? <label className="text-xs text-muted-foreground">允许集号<Input value={episodes} onChange={(event) => setEpisodes(event.target.value)} placeholder="1, 2, 3" className="mt-1" /></label> : null}
          <label className="text-xs text-muted-foreground">已落地文件名（每行一个）<Textarea value={files} onChange={(event) => setFiles(event.target.value)} className="mt-1 min-h-40 resize-y font-mono text-xs leading-6" /></label>
          <Button className="w-full" disabled={busy} onClick={() => void preview()}><Play className="size-4" />生成整理计划</Button>
        </CardContent></Card>

        <Card className="min-w-0 rounded-md shadow-none"><CardHeader><CardTitle className="text-base">路径映射</CardTitle><CardDescription>计划使用当前 Organizer 命名模板；目标目录为相对于已选 115 库目录的路径。</CardDescription></CardHeader><CardContent className="min-w-0">
          {items.length === 0 ? <div className="flex min-h-64 items-center justify-center border border-dashed text-center text-sm text-muted-foreground">填写资源信息后生成预览<br />不会执行任何转存、移动或改名</div> : <div className="w-full overflow-x-auto"><Table><TableHeader><TableRow><TableHead className="whitespace-nowrap">原文件</TableHead><TableHead className="whitespace-nowrap">目标目录</TableHead><TableHead className="whitespace-nowrap">目标文件名</TableHead></TableRow></TableHeader><TableBody>{items.map((item) => <TableRow key={item.src_id}><TableCell><span className="block max-w-[220px] truncate font-mono text-xs">{item.src_name}</span></TableCell><TableCell><div className="flex min-w-[220px] items-center gap-2 text-xs"><ArrowRight className="size-4 shrink-0 text-primary" /><span className="font-mono">{item.target_dir || '/'}</span></div></TableCell><TableCell><span className="block max-w-[320px] truncate font-mono text-xs text-primary">{item.target_name}</span></TableCell></TableRow>)}</TableBody></Table></div>}
        </CardContent></Card>
      </div>
    </div>
  );
}
