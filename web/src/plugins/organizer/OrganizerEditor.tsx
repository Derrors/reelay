import { FolderTree } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { EditorHeading } from '../EditorHeading';
import { obj, text, type PluginEditorProps } from '../types';
import { CategoryList } from './CategoryList';
import { LAYOUTS } from './layouts';
import { NamingEditor } from './NamingEditor';
import { OrganizePreviewDialog } from './OrganizePreviewDialog';
import { TypeDirsFlow } from './TypeDirsFlow';

/** 媒体库整理（organizer）的设置：目录方式、按媒体类型的文件夹名、命名模板。 */
export function OrganizerEditor({ section, setSection }: PluginEditorProps) {
  const layout = text(section.layout) || 'category';
  const [previewing, setPreviewing] = useState(false);
  const naming = obj(section.naming);
  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <EditorHeading
          title="存放目录"
          description="文件夹的第一级与某个保存目录同名时（如「剧集」），直接存进那个保存目录；否则在来源的保存目录下新建。审批时手动指定的目录优先。"
        />
        <div role="radiogroup" aria-label="目录方式" className="grid gap-2 sm:grid-cols-3">
          {LAYOUTS.map(option => {
            const active = layout === option.value;
            return (
              <button
                key={option.value} type="button" role="radio" aria-checked={active}
                onClick={() => setSection(s => ({ ...s, layout: option.value }))}
                className={cn(
                  'min-w-0 rounded-lg border bg-card p-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-ring',
                  active ? 'border-primary/60 ring-2 ring-primary/15' : 'border-border hover:border-primary/40',
                )}
              >
                <span className="flex items-center gap-2 text-[13px] font-medium">
                  <span className={cn('grid size-3.5 shrink-0 place-items-center rounded-full border', active ? 'border-primary' : 'border-muted-foreground/40')}>
                    {active ? <span className="size-1.5 rounded-full bg-primary" /> : null}
                  </span>
                  {option.label}
                </span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">{option.description}</span>
                <code className="mt-1.5 block truncate font-mono text-[11px] text-foreground/80" title={option.example}>{option.example}</code>
              </button>
            );
          })}
        </div>
        {layout === 'category' ? <CategoryList /> : null}
        {layout === 'media_type' ? <TypeDirsFlow section={section} setSection={setSection} /> : null}
      </div>
      <div className="border-t border-border/70 pt-5">
        <NamingEditor section={section} setSection={setSection} />
        <div className="mt-4 flex flex-col gap-2 rounded-lg border border-border/70 bg-background/60 px-3.5 py-2.5 sm:flex-row sm:items-center">
          <p className="min-w-0 flex-1 text-xs leading-5 text-muted-foreground">
            贴几个转存后的文件名，看按上面的模板（包括还没保存的修改）会整理成什么样。
          </p>
          <Button size="sm" variant="outline" className="shrink-0 self-start bg-card sm:self-auto" onClick={() => setPreviewing(true)}>
            <FolderTree />预览整理效果
          </Button>
        </div>
        {previewing ? (
          <OrganizePreviewDialog open onOpenChange={setPreviewing}
            naming={{ movie: text(naming.movie) || undefined, tv: text(naming.tv) || undefined }} />
        ) : null}
      </div>
    </div>
  );
}
