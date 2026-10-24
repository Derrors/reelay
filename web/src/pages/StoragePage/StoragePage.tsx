import { ArrowRight, Check, ChevronRight, CircleAlert, Copy, Folder, FolderOpen, Hash, House, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { EmptyState } from '@/components/app/EmptyState';
import { Page } from '@/components/app/Page';
import { Panel } from '@/components/app/Panel';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useDirListing } from '@/hooks/useDirListing';
import { cn } from '@/lib/utils';

/** 复制文本；非 HTTPS 访问时浏览器不提供剪贴板 API，退回到 execCommand。 */
async function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.append(area);
  area.select();
  const ok = document.execCommand('copy');
  area.remove();
  if (!ok) throw new Error('copy failed');
}

function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef(0);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = (id: string) => {
    copyText(id).then(() => {
      setCopied(id);
      toast.success(`目录编号 ${id} 已复制`);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(null), 1600);
    }).catch(() => toast.error('无法访问剪贴板，请手动复制'));
  };
  return { copied, copy };
}

export default function StoragePage() {
  const dir = useDirListing();
  const [manualCid, setManualCid] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const { copied, copy } = useCopy();
  const { cid: current, path, items, next } = dir;
  const busy = dir.loading || dir.loadingMore;
  const error = dir.loading ? '' : dir.error;

  const jump = () => {
    const cid = manualCid.trim();
    if (!/^[0-9]+$/.test(cid)) {
      toast.error('目录编号必须是纯数字');
      return;
    }
    dir.open(cid, cid === '0' ? [] : [{ id: cid, name: `目录 ${cid}` }]);
  };

  const firstLoad = !dir.ready && items.length === 0 && !error;

  return (
    <Page
      title="115 目录"
      description="只读浏览你的 115 网盘，确认保存位置是否正确；不会创建、移动或删除任何文件。"
      actions={(
        <Button variant="outline" className="bg-card" disabled={busy} onClick={dir.reload}>
          <RefreshCw className={cn(busy && 'animate-spin')} />刷新
        </Button>
      )}
    >
      <Panel bodyClassName="p-0">
        <div className="flex items-center gap-3 border-b border-border/70 px-3 py-2.5 sm:px-4">
          <nav aria-label="当前位置" className="min-w-0 flex-1 overflow-x-auto">
            <ol className="flex items-center gap-0.5 whitespace-nowrap text-sm">
              <li>
                <button
                  type="button"
                  disabled={busy || path.length === 0}
                  onClick={() => dir.open('0', [])}
                  className={cn(
                    'inline-flex min-h-9 items-center gap-1.5 rounded-md px-2 font-medium transition-colors sm:min-h-8',
                    path.length ? 'text-muted-foreground hover:bg-muted hover:text-foreground' : 'text-foreground',
                  )}
                >
                  <House className="size-4" />全部文件
                </button>
              </li>
              {path.map((crumb, i) => {
                const last = i === path.length - 1;
                return (
                  <li key={crumb.id} className="flex items-center gap-0.5">
                    <ChevronRight className="size-3.5 shrink-0 text-muted-foreground/60" />
                    <button
                      type="button"
                      disabled={busy || last}
                      aria-current={last ? 'page' : undefined}
                      onClick={() => dir.open(crumb.id, path.slice(0, i + 1))}
                      className={cn(
                        'inline-flex min-h-9 max-w-56 items-center rounded-md px-2 font-medium transition-colors sm:min-h-8',
                        last ? 'text-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                      )}
                    >
                      <span className="truncate">{crumb.name}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>
          {current !== '0' ? (
            <button
              type="button"
              onClick={() => copy(current)}
              title="复制当前目录编号"
              className="hidden min-h-8 shrink-0 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground sm:inline-flex"
            >
              <Hash className="size-3.5" />
              <span className="font-mono tabular-nums">{current}</span>
              {copied === current ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
            </button>
          ) : null}
        </div>

        {error ? (
          <div role="alert" className="flex flex-col items-center px-6 py-14 text-center">
            <span className="grid size-11 place-items-center rounded-full bg-destructive/10 text-destructive">
              <CircleAlert className="size-5" />
            </span>
            <p className="mt-4 text-sm font-medium">无法读取这个目录</p>
            <p className="mt-1 max-w-sm text-xs leading-5 text-muted-foreground">{error}</p>
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              <Button variant="outline" onClick={dir.reload}><RefreshCw />重新查询</Button>
              <Button asChild variant="ghost" className="text-primary">
                <Link to="/config?tab=connections">检查 115 连接<ArrowRight /></Link>
              </Button>
            </div>
          </div>
        ) : firstLoad ? (
          <div className="space-y-1 p-2" role="status" aria-label="正在读取目录">
            {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-11 rounded-lg" />)}
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title="这里没有子文件夹"
            description={next !== null ? '可以继续加载后面的内容。' : '这个页面只列出文件夹，不显示文件。'}
            className="m-4 border-none py-12"
          />
        ) : (
          <ul className={cn('p-1.5 transition-opacity sm:p-2', busy && 'opacity-60')}>
            {items.map((item) => (
              <li key={item.id} className="group flex items-center gap-1 rounded-lg transition-colors hover:bg-muted/70">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => dir.open(item.id, [...path, { id: item.id, name: item.name }])}
                  className="flex min-h-11 min-w-0 flex-1 items-center gap-3 rounded-lg px-2.5 text-left"
                >
                  <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                    <Folder className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{item.name}</span>
                  <span className="hidden font-mono text-[11px] tabular-nums text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 sm:inline">
                    {item.id}
                  </span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground/50" />
                </button>
                <button
                  type="button"
                  aria-label={`复制「${item.name}」的目录编号`}
                  title="复制目录编号"
                  onClick={() => copy(item.id)}
                  className="mr-1 grid size-9 shrink-0 place-items-center rounded-md text-muted-foreground transition hover:bg-card hover:text-foreground focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                >
                  {copied === item.id ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}
                </button>
              </li>
            ))}
          </ul>
        )}

        {next !== null && !error ? (
          <div className="border-t border-border/70 p-3 text-center">
            <Button variant="outline" disabled={busy} onClick={() => void dir.loadMore()}>加载更多</Button>
          </div>
        ) : null}
      </Panel>

      <div className="mt-4 px-1">
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          aria-expanded={showAdvanced}
          className="inline-flex min-h-9 items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground sm:min-h-8"
        >
          <Hash className="size-3.5" />{showAdvanced ? '收起按编号定位' : '知道目录编号？直接定位'}
        </button>
        {showAdvanced ? (
          <form className="mt-2 flex max-w-md gap-2" onSubmit={(e) => { e.preventDefault(); jump(); }}>
            <Input
              aria-label="目录编号"
              inputMode="numeric"
              value={manualCid}
              onChange={(e) => setManualCid(e.target.value)}
              className="bg-card font-mono"
              placeholder="输入目录编号"
            />
            <Button type="submit" variant="outline" className="bg-card" disabled={busy || !manualCid.trim()}>定位</Button>
          </form>
        ) : null}
        <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
          一般使用不需要关心目录编号，只在对接外部工具时才用得到。保存目录在
          <Link to="/config" className="mx-0.5 font-medium text-primary hover:underline">设置</Link>中修改。
        </p>
      </div>
    </Page>
  );
}
