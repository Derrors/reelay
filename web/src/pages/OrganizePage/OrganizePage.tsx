import { useState } from 'react';
import { ArrowRight, FileQuestion, FolderTree, Play, Wand2 } from 'lucide-react';
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
      toast.error('请先填写片名，并至少填写一个文件名');
      return;
    }
    setBusy(true);
    try {
      const result = await api.organizePreview({ ...parsed, episodes: numbers(episodes), is_media: true }, landed);
      setItems(result.items);
      toast.success('整理方案已生成');
    } catch (err) {
      setItems([]);
      toast.error(err instanceof Error ? err.message : '无法生成整理方案');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-7xl p-5 sm:p-8">
      <PageHeader eyebrow="整理预览" title="改名归档效果提前看" description="想确认转存后文件会被整理成什么样子？在这里填写资源信息和文件名，系统会展示整理后的文件夹结构与文件名。仅预览，不会对网盘做任何改动。" />

      <div className="mt-6 grid min-w-0 gap-6 xl:grid-cols-[380px_minmax(0,1fr)]">
        <Card className="h-fit rounded-xl border-border/70 shadow-sm">
          <CardHeader className="p-5 pb-0">
            <CardTitle className="flex items-center gap-2 text-[15px]"><Wand2 className="size-[18px] text-primary" />资源信息</CardTitle>
            <CardDescription>填写得越准确，预览结果越接近真实整理效果。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 p-5">
            <label className="block text-xs text-muted-foreground">资源类型
              <NativeSelect value={parsed.media_type ?? 'tv'} onChange={(event) => setParsed({ ...parsed, media_type: event.target.value })} className="mt-1 h-10 w-full rounded-lg">
                <NativeSelectOption value="tv">剧集</NativeSelectOption>
                <NativeSelectOption value="movie">电影</NativeSelectOption>
              </NativeSelect>
            </label>
            <label className="block text-xs text-muted-foreground">片名
              <Input value={parsed.title ?? ''} onChange={(event) => setParsed({ ...parsed, title: event.target.value })} placeholder="例如：法医秦明之龙番往事" className="mt-1 h-10 rounded-lg" />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-xs text-muted-foreground">年份<Input type="number" className="mt-1 h-10 rounded-lg" value={parsed.year ?? ''} onChange={(event) => setParsed({ ...parsed, year: Number(event.target.value) || null })} /></label>
              <label className="block text-xs text-muted-foreground">第几季<Input type="number" className="mt-1 h-10 rounded-lg" value={parsed.season ?? ''} onChange={(event) => setParsed({ ...parsed, season: Number(event.target.value) || null })} /></label>
            </div>
            {parsed.media_type === 'tv' ? <label className="block text-xs text-muted-foreground">包含哪几集（用逗号分隔）<Input className="mt-1 h-10 rounded-lg" value={episodes} onChange={(event) => setEpisodes(event.target.value)} placeholder="1, 2, 3" /></label> : null}
            <label className="block text-xs text-muted-foreground">转存后的文件名（每行一个）
              <Textarea value={files} onChange={(event) => setFiles(event.target.value)} className="mt-1 min-h-40 resize-y text-xs leading-6" />
            </label>
            <Button className="h-10 w-full rounded-lg" disabled={busy} onClick={() => void preview()}>{busy ? '正在生成…' : <><Play className="size-4" />生成整理方案</>}</Button>
          </CardContent>
        </Card>

        <Card className="min-w-0 rounded-xl border-border/70 shadow-sm">
          <CardHeader className="p-5 pb-0">
            <CardTitle className="flex items-center gap-2 text-[15px]"><FolderTree className="size-[18px] text-primary" />整理后的文件结构</CardTitle>
            <CardDescription>使用当前设置的命名规则，符合 Jellyfin / Emby 刮削规范。</CardDescription>
          </CardHeader>
          <CardContent className="min-w-0 p-5">
            {items.length === 0 ? (
              <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border border-dashed text-center">
                <FileQuestion className="size-8 text-muted-foreground/50" />
                <p className="mt-3 text-sm text-muted-foreground">填好左边信息后生成预览</p>
                <p className="mt-1 text-xs text-muted-foreground/80">不会执行任何转存、移动或改名操作</p>
              </div>
            ) : (
              <div className="w-full overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader><TableRow className="bg-muted/40 hover:bg-muted/40"><TableHead className="h-10">现在的文件名</TableHead><TableHead>整理后的文件夹</TableHead><TableHead>整理后的文件名</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {items.map((item) => (
                      <TableRow key={item.src_id} className="border-border/60">
                        <TableCell><span className="block max-w-[220px] truncate text-xs text-muted-foreground">{item.src_name}</span></TableCell>
                        <TableCell><div className="flex min-w-[220px] items-center gap-2 text-xs"><ArrowRight className="size-4 shrink-0 text-primary/60" /><span className="font-mono">{item.target_dir || '/'}</span></div></TableCell>
                        <TableCell><span className="block max-w-[320px] truncate text-xs font-medium text-primary">{item.target_name}</span></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
