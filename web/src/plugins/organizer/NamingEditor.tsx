import { useState } from 'react';
import { ExternalLink, Eye, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { configApi } from '@/api/config';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { MEDIA_TYPE_LABELS } from '@/lib/status';
import { cn, errorMessage } from '@/lib/utils';
import { EditorHeading } from '../EditorHeading';
import { at, text } from '@/lib/values';
import type { PluginEditorProps } from '../types';

type Kind = 'movie' | 'tv';

// 变量与 MoviePilot 的重命名格式一致，可直接粘贴其模板
const NAMING_VARS: Array<[string, string]> = [
  ['title', '片名'], ['original_title', '原名'], ['name', '文件名里的片名'], ['en_name', '文件名里的英文名'],
  ['year', '年份'], ['tmdbid', 'TMDB 编号'], ['season', '季号'], ['episode', '集号'], ['season_episode', 'S01E02'],
  ['part', '分段'], ['videoFormat', '清晰度'], ['videoCodec', '视频编码'], ['audioCodec', '音频编码'],
  ['resourceType', '片源'], ['effect', '特效'], ['edition', '版本'], ['releaseGroup', '发布组'],
  ['fileExt', '后缀（含点）'], ['original_name', '原文件名'],
];

export function NamingEditor({ section, setSection }: PluginEditorProps) {
  const naming = at(section, 'naming');
  const [preview, setPreview] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState<Kind | null>(null);
  const update = (kind: Kind, value: string) => {
    setSection(s => ({ ...s, naming: { ...at(s, 'naming'), [kind]: value } }));
    setPreview(p => ({ ...p, [kind]: '' }));
  };
  const test = async (kind: Kind) => {
    setTesting(kind);
    try {
      const r = await configApi.extensionAction<{ path: string }>('organizer', 'preview_naming', { kind, template: text(naming[kind]) });
      setPreview(p => ({ ...p, [kind]: r.path }));
    } catch (e) {
      toast.error(errorMessage(e, '模板预览失败'));
    } finally {
      setTesting(null);
    }
  };
  return (
    <div className="space-y-4">
      <EditorHeading
        title="文件命名模板"
        description={<>用占位符拼出整理后的目录与文件名，如 <code className="rounded bg-muted px-1 font-mono text-[11px]">{'{{title}} ({{year}})'}</code>；变量与 MoviePilot 的重命名格式一致，可直接沿用。保存前会自动检查模板，不会改动网盘里已有的文件。</>}
        action={(
          <a href="https://jinja.palletsprojects.com/en/3.0.x/templates/" target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
            模板语法说明<ExternalLink className="size-3" />
          </a>
        )}
      />
      {(['movie', 'tv'] as const).map(kind => (
        <div key={kind} className="space-y-2.5 rounded-lg border border-border bg-card p-3 sm:p-4">
          <label className="block space-y-1.5">
            <span className="block text-[13px] font-medium">{MEDIA_TYPE_LABELS[kind]}</span>
            <Textarea
              aria-label={`${MEDIA_TYPE_LABELS[kind]}命名模板`} spellCheck={false}
              className="min-h-20 break-all font-mono text-xs leading-5 md:text-xs"
              value={text(naming[kind])} onChange={e => update(kind, e.target.value)}
            />
          </label>
          <div className="flex items-start gap-2">
            <div className={cn(
              'min-h-8 min-w-0 flex-1 rounded-md bg-muted/70 px-3 py-1.5 text-xs leading-5',
              preview[kind] ? 'break-all font-mono text-foreground' : 'text-muted-foreground',
            )}>
              {preview[kind] || '点「预览」，用示例影片查看改名效果'}
            </div>
            <Button type="button" variant="outline" size="sm" className="shrink-0" disabled={testing === kind} onClick={() => void test(kind)}>
              {testing === kind ? <Loader2 className="animate-spin" /> : <Eye />}预览
            </Button>
          </div>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        <span>可用信息：</span>
        {NAMING_VARS.map(([key, label]) => (
          <span key={key} className="rounded-md border border-border bg-card px-1.5 py-0.5">
            {label} <code className="font-mono text-[11px] text-foreground">{key}</code>
          </span>
        ))}
      </div>
    </div>
  );
}
