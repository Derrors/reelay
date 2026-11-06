import { cn } from '@/lib/utils';

const CELLS_UP_TO = 60;

/** 一季的集数进度：已入库、缺的（已播未入库）、还没播的。集数不多时逐集画格子，多了画成一条。 */
export function EpisodeProgress({ have, missing, total, aired, className }: {
  have: number[]; missing: number[]; total: number | null; aired: number | null; className?: string;
}) {
  const haveSet = new Set(have);
  const missingSet = new Set(missing);
  const count = Math.max(total ?? 0, aired ?? 0, ...have, ...missing);
  if (!count) return null;
  const stateOf = (ep: number) => (haveSet.has(ep) ? 'have' : missingSet.has(ep) ? 'missing' : aired !== null && ep <= aired ? 'aired' : 'future');
  const color = { have: 'bg-primary', missing: 'bg-warning', aired: 'bg-hover-2', future: 'bg-muted' };
  if (count <= CELLS_UP_TO) {
    return (
      <div className={cn('flex gap-[2px]', className)} role="img"
        aria-label={`已入库 ${have.length} 集${missing.length ? `，缺 ${missing.length} 集` : ''}，共 ${count} 集`}>
        {Array.from({ length: count }, (_, i) => i + 1).map((ep) => (
          <span key={ep} title={`第 ${ep} 集 · ${{ have: '已入库', missing: '缺', aired: '已播', future: '未播' }[stateOf(ep)]}`}
            className={cn('h-1.5 min-w-0 flex-1 rounded-[1px] transition-colors', color[stateOf(ep)])} />
        ))}
      </div>
    );
  }
  const pct = (n: number) => `${Math.min(100, (n / count) * 100)}%`;
  return (
    <div className={cn('flex h-1.5 overflow-hidden rounded-full bg-muted', className)} role="img"
      aria-label={`已入库 ${have.length} / ${count} 集`}>
      <span className="bg-primary" style={{ width: pct(have.length) }} />
      <span className="bg-warning" style={{ width: pct(missing.length) }} />
    </div>
  );
}
