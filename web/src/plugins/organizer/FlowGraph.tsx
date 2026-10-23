import type { ReactNode } from 'react';
import { Clapperboard, Folder } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

/** 目录方式的流程图（二级分类、按媒体类型共用）：点阵画布、虚线连接的节点，从左往右读。 */

const Dot = ({ className }: { className?: string }) => (
  <span aria-hidden className={cn('absolute top-1/2 z-10 size-2 -translate-y-1/2 rounded-full bg-primary', className)} />
);

/** 横向树：节点右侧接一段虚线到竖向主干，主干再分出虚线连到每个子节点；节点与子节点组垂直居中。 */
export function Branch({ node, children = [] }: { node: ReactNode; children?: ReactNode[] }) {
  return (
    <div className="flex items-center">
      <div className="relative shrink-0">
        {node}
        {children.length ? <Dot className="-right-1" /> : null}
      </div>
      {children.length ? (
        <>
          {/* 与子节点的横线一样按 top: 50% 定位，两段严格对齐（按自身高度居中会差半个像素） */}
          <span
            aria-hidden
            className="relative w-5 shrink-0 self-stretch after:absolute after:inset-x-0 after:top-1/2 after:border-t after:border-dashed after:border-primary/70"
          />
          <div className="flex flex-col">
            {children.map((child, i) => (
              <div
                key={i}
                className={cn(
                  'relative flex items-center py-1.5 pl-5',
                  // 竖向主干：第一个子节点从中间往下，最后一个从顶部到中间，只有一个子节点时不画
                  'before:absolute before:inset-y-0 before:left-0 before:border-l before:border-dashed before:border-primary/70',
                  'first:before:top-1/2 last:before:bottom-1/2 only:before:hidden',
                  'after:absolute after:left-0 after:top-1/2 after:w-5 after:border-t after:border-dashed after:border-primary/70',
                )}
              >
                <Dot className="left-5 -translate-x-1/2" />
                {child}
              </div>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

export function GraphCanvas({ children }: { children: ReactNode }) {
  return (
    <div
      className={cn(
        'overflow-x-auto rounded-xl border border-border/70 bg-muted/40',
        // 点阵画布：用主题色混出淡色圆点，深色模式自动跟随
        '[background-image:radial-gradient(color-mix(in_oklab,var(--color-muted-foreground)_28%,transparent)_1px,transparent_1px)]',
        '[background-size:18px_18px]',
      )}
    >
      <div className="mx-auto w-fit p-6">{children}</div>
    </div>
  );
}

export function NodeCard({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('rounded-lg border border-border bg-card shadow-xs', className)}>{children}</div>;
}

export function RootNode() {
  return (
    <NodeCard className="w-32 rounded-xl px-3 py-3 text-center">
      <span className="mx-auto grid size-8 place-items-center rounded-full bg-primary/10 text-primary">
        <Clapperboard className="size-4" />
      </span>
      <p className="mt-1.5 text-[13px] font-semibold">待整理的作品</p>
      <p className="text-[11px] text-muted-foreground">按 TMDB 识别结果</p>
    </NodeCard>
  );
}

/** 一条匹配规则：编号即比对顺序；兜底或不生效的规则用虚线框。 */
export function RuleNode({ order, hint, dashed, children }: { order: number; hint: string; dashed?: boolean; children: ReactNode }) {
  return (
    <NodeCard className={cn('flex w-64 items-start gap-2 px-2.5 py-2', dashed && 'border-dashed shadow-none')}>
      <span
        className="mt-px grid size-5 shrink-0 place-items-center rounded-full bg-muted text-[11px] font-semibold tabular-nums text-muted-foreground"
        title={hint}
      >
        {order}
      </span>
      <div className="flex min-w-0 flex-wrap gap-1">{children}</div>
    </NodeCard>
  );
}

const FOLDER = 'border-l-[3px] border-l-primary/70';

/** 归入的文件夹（只读）：一级目录弱化，二级目录加粗。 */
export function FolderNode({ path }: { path: string }) {
  const [first, second] = path.split('/');
  return (
    <NodeCard className={cn('w-44 px-3 py-2', FOLDER)}>
      <p className="flex min-w-0 items-center gap-1.5 text-[13px] leading-5">
        <Folder className="size-3.5 shrink-0 text-primary" />
        <span className="shrink-0 text-muted-foreground">{first}</span>
        {second ? <><span className="shrink-0 text-muted-foreground/50">/</span><span className="truncate font-medium">{second}</span></> : null}
      </p>
    </NodeCard>
  );
}

/** 归入的文件夹（可编辑）：在图上直接改文件夹名。 */
export function FolderInputNode({ label, value, placeholder, onChange }: {
  label: string; value: string; placeholder: string; onChange: (value: string) => void;
}) {
  return (
    <NodeCard className={cn('flex w-48 items-center gap-1.5 py-1 pl-3 pr-1', FOLDER)}>
      <Folder className="size-3.5 shrink-0 text-primary" />
      <Input
        aria-label={label} value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)}
        className="h-7 border-transparent bg-transparent px-1.5 text-[13px] font-medium shadow-none hover:border-border focus-visible:border-ring md:text-[13px]"
      />
    </NodeCard>
  );
}
