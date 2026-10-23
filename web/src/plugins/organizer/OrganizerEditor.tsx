import { cn } from '@/lib/utils';
import { EditorHeading } from '../EditorHeading';
import { text, type PluginEditorProps } from '../types';
import { CategoryList } from './CategoryList';
import { LAYOUTS } from './layouts';
import { NamingEditor } from './NamingEditor';
import { TypeDirsFlow } from './TypeDirsFlow';

/** 媒体库整理（organizer）的设置：目录方式、按媒体类型的文件夹名、命名模板。 */
export function OrganizerEditor({ section, setSection }: PluginEditorProps) {
  const layout = text(section.layout) || 'category';
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
      </div>
    </div>
  );
}
