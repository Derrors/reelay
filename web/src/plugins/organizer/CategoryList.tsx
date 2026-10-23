import { useEffect, useState } from 'react';
import { Film, Lock, Tv } from 'lucide-react';
import { api } from '@/api/client';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { Branch, FolderNode, GraphCanvas, NodeCard, RootNode, RuleNode } from './FlowGraph';
import type { ICategoryGroup } from './types';

type Media = ICategoryGroup['media'];
type Condition = ICategoryGroup['categories'][number]['conditions'][number];

const MEDIA: Record<Media, { label: string; icon: typeof Film; accent: string }> = {
  movie: { label: '电影', icon: Film, accent: 'border-l-info' },
  tv: { label: '剧集', icon: Tv, accent: 'border-l-primary' },
};
const MAX_VALUES = 3;

function summarize(condition: Condition) {
  const names = condition.values.map(v => v.name);
  const shown = names.length > MAX_VALUES ? `${names.slice(0, MAX_VALUES).join('、')} 等 ${names.length} 个` : names.join('、');
  return { shown, full: `${condition.label}：${names.join('、')}（${condition.values.map(v => v.code).join(',')}）` };
}

function MediaNode({ group }: { group: ICategoryGroup }) {
  const { label, icon: Icon, accent } = MEDIA[group.media];
  return (
    <NodeCard className={cn('w-36 border-l-[3px] px-3 py-2.5', accent)}>
      <p className="flex items-center gap-1.5 text-[13px] font-semibold">
        <Icon className="size-3.5 text-muted-foreground" />{label}
      </p>
      <p className="mt-0.5 text-[11px] text-muted-foreground">TMDB 类型 · {group.categories.length} 条规则</p>
    </NodeCard>
  );
}

/** 二级分类只读展示：规则随插件发布（category.yaml）。从左往右读：作品按 TMDB 类型分流，
 *  再按编号从上到下逐条比对条件，归入第一个满足的文件夹。 */
export function CategoryList() {
  const [groups, setGroups] = useState<ICategoryGroup[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    api.extensionAction<{ media: ICategoryGroup[] }>('organizer', 'category_rules')
      .then(r => { if (active) setGroups(r.media); })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : '读取分类规则失败'); });
    return () => { active = false; };
  }, []);
  if (error) return <p className="text-xs text-destructive">{error}</p>;
  if (!groups) return <Skeleton className="h-96 rounded-xl" />;
  const paths = groups.flatMap(g => g.categories.map(c => c.path));
  const roots = new Set(paths.map(p => p.split('/')[0]));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
          从左往右：作品按 TMDB 类型分流，再按编号从上到下逐条比对，归入第一个满足条件的文件夹；都不满足时直接放在保存目录下。
          整理后网盘里会有 <b className="font-semibold tabular-nums text-foreground">{roots.size}</b> 个一级文件夹、
          <b className="font-semibold tabular-nums text-foreground">{paths.length}</b> 个分类。
        </p>
        <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
          title="规则随插件发布，不能在管理台修改">
          <Lock className="size-3" />只读 · <code className="font-mono">category.yaml</code>
        </span>
      </div>
      <GraphCanvas>
        <Branch
          node={<RootNode />}
          children={groups.map(group => (
            <Branch
              key={group.media}
              node={<MediaNode group={group} />}
              children={group.categories.map((rule, i) => {
                const label = MEDIA[group.media].label;
                return (
                  <Branch
                    key={rule.path}
                    node={(
                      <RuleNode order={i + 1} hint={`${label}的第 ${i + 1} 条规则`} dashed={!rule.conditions.length}>
                        {rule.conditions.length ? rule.conditions.map(condition => {
                          const { shown, full } = summarize(condition);
                          return (
                            <span key={condition.field} title={full} className="rounded bg-muted px-1.5 py-px text-[11px] text-foreground/85">
                              <span className="mr-1 text-muted-foreground">{condition.label}</span>{shown}
                            </span>
                          );
                        }) : <span className="py-px text-xs text-muted-foreground">其余{label}（兜底）</span>}
                      </RuleNode>
                    )}
                    children={[<FolderNode key="folder" path={rule.path} />]}
                  />
                );
              })}
            />
          ))}
        />
      </GraphCanvas>
    </div>
  );
}
