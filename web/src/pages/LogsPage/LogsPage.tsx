import { ArrowDown, CircleAlert, Pause, Play, RefreshCw, ScrollText, Search, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { logApi } from '@/api/logs';
import { EmptyState } from '@/components/app/EmptyState';
import { ErrorState } from '@/components/app/ErrorState';
import { Page } from '@/components/app/Page';
import { Panel } from '@/components/app/Panel';
import { Segmented, SegmentCount } from '@/components/app/Segmented';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useAsync } from '@/hooks/useAsync';
import { useDebounced } from '@/hooks/useDebounced';
import { confirmDialog } from '@/lib/confirm';
import { cn, errorMessage } from '@/lib/utils';

const FETCH_LIMIT = 600;
const REFRESH_MS = 5000;

type Level = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
type LevelFilter = 'all' | 'warning' | 'error';

interface LogLine {
  key: number;
  time: string;
  level: Level;
  logger: string;
  message: string;
  /** 不带时间与级别的续行（如异常堆栈），沿用上一行的级别。 */
  continued: boolean;
}

// 后端格式：%(asctime)s %(levelname)s %(name)s | %(message)s，时间为 YYYY-MM-DD HH:MM:SS
const LINE_RE = /^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:[.,]\d+)?\s+(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s+(\S+)\s+\|\s?(.*)$/;

function parse(lines: string[]): LogLine[] {
  let level: Level = 'INFO';
  return lines.map((raw, key) => {
    const m = LINE_RE.exec(raw);
    if (!m) return { key, time: '', level, logger: '', message: raw, continued: true };
    const name = m[3];
    level = name === 'CRITICAL' ? 'ERROR' : name === 'WARN' ? 'WARNING' : (name as Level);
    // 显示「月-日 时:分:秒」，跨天的日志也分得清
    return { key, time: `${m[1]} ${m[2]}`, level, logger: m[4], message: m[5], continued: false };
  });
}

const LEVEL_STYLE: Record<Level, { label: string; text: string; row: string }> = {
  DEBUG: { label: 'DEBUG', text: 'text-muted-foreground', row: '' },
  INFO: { label: 'INFO', text: 'text-info', row: '' },
  WARNING: { label: 'WARN', text: 'text-warning', row: 'bg-warning/[0.07]' },
  ERROR: { label: 'ERROR', text: 'text-destructive', row: 'bg-destructive/[0.07]' },
};

function highlight(text: string, query: string): ReactNode {
  if (!query) return text;
  const lower = text.toLowerCase();
  const needle = query.toLowerCase();
  const out: ReactNode[] = [];
  let from = 0;
  for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, from)) {
    if (at > from) out.push(text.slice(from, at));
    out.push(<mark key={at} className="rounded-[3px] bg-warning/25 px-px text-inherit">{text.slice(at, at + needle.length)}</mark>);
    from = at + needle.length;
  }
  out.push(text.slice(from));
  return out;
}

export default function LogsPage() {
  const [query, setQuery] = useState('');
  const submitted = useDebounced(query);
  const [filter, setFilter] = useState<LevelFilter>('all');
  const [auto, setAuto] = useState(true);
  const [atBottom, setAtBottom] = useState(true);
  const viewport = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // 换了搜索词时旧结果直接作废；自动刷新的多个请求按发出顺序生效（见 useAsync）
  const logs = useAsync(() => logApi.list(FETCH_LIMIT, submitted || undefined).then((r) => ({ ...r, query: submitted })),
    [submitted], '读取日志失败');
  const { reload } = logs;
  useEffect(() => {
    if (!auto) return;
    const timer = window.setInterval(() => { if (!document.hidden) reload(); }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [auto, reload]);

  const lines = useMemo(() => (logs.data ? (logs.data.exists ? logs.data.lines : []) : null), [logs.data]);
  const missing = logs.data ? !logs.data.exists : false;
  const path = logs.data?.path ?? '';
  const error = logs.error;
  /** 当前显示的日志对应的搜索词；stale 时新搜索词的结果还在路上。 */
  const shownQuery = logs.data?.query ?? '';

  const [clearing, setClearing] = useState(false);
  const clear = async () => {
    if (!(await confirmDialog({
      title: '清空运行日志？',
      description: '包括轮转留下的旧日志文件，清空后无法恢复。',
      confirmText: '清空日志', destructive: true,
    }))) return;
    setClearing(true);
    try {
      const r = await logApi.clear();
      toast.success(`日志已清空，释放 ${(r.freed / 1024 / 1024).toFixed(1)} MB`);
      reload();
    } catch (e) {
      toast.error(errorMessage(e, '清空日志失败'));
    } finally {
      setClearing(false);
    }
  };

  const parsed = useMemo(() => parse(lines ?? []), [lines]);
  // 按日志条目计数，异常堆栈的续行不重复计入。
  const counts = useMemo(() => {
    const entries = parsed.filter((l) => !l.continued);
    return {
      all: entries.length,
      warning: entries.filter((l) => l.level === 'WARNING' || l.level === 'ERROR').length,
      error: entries.filter((l) => l.level === 'ERROR').length,
    };
  }, [parsed]);
  const visible = useMemo(() => parsed.filter((l) => (
    filter === 'all' || l.level === 'ERROR' || (filter === 'warning' && l.level === 'WARNING')
  )), [parsed, filter]);

  // 停在底部时，新日志到达后继续跟随；用户向上翻看时不打扰。
  useLayoutEffect(() => {
    const el = viewport.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [visible]);

  const onScroll = () => {
    const el = viewport.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
    stick.current = near;
    setAtBottom(near);
  };

  const toBottom = () => {
    const el = viewport.current;
    if (!el) return;
    stick.current = true;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  };

  const filters: Array<{ value: LevelFilter; label: string; count: number; tone?: 'warning' | 'danger' }> = [
    { value: 'all', label: '全部', count: counts.all },
    { value: 'warning', label: '警告及以上', count: counts.warning, tone: 'warning' },
    { value: 'error', label: '错误', count: counts.error, tone: 'danger' },
  ];

  let body: ReactNode;
  if (error && lines === null) {
    body = <ErrorState title="读取日志失败" error={error} onRetry={reload} retrying={logs.loading} className="m-4 border-none" />;
  } else if (lines === null) {
    body = <div className="space-y-2 p-4">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-4" style={{ width: `${90 - i * 9}%` }} />)}</div>;
  } else if (missing) {
    body = (
      <EmptyState
        icon={ScrollText}
        title="还没有日志文件"
        description="后台服务运行并处理消息后，这里会自动出现日志。"
        className="m-4 border-none"
      />
    );
  } else {
    body = (
      <div className="relative">
        <div
          ref={viewport}
          onScroll={onScroll}
          role="log"
          aria-live="off"
          aria-label="运行日志"
          className={cn(
            'h-[62vh] min-h-[360px] overflow-auto bg-muted/35 py-2 font-mono text-xs leading-5 transition-opacity',
            logs.stale && 'opacity-60',
          )}
        >
          {visible.length ? visible.map((line) => {
            const style = LEVEL_STYLE[line.level];
            return (
              <div key={line.key} className={cn('flex gap-3 px-4 py-px hover:bg-foreground/[0.04]', style.row)}>
                <span className="w-[14ch] shrink-0 tabular-nums text-muted-foreground/80">{line.time}</span>
                <span className={cn('w-[5ch] shrink-0 font-semibold', line.continued ? 'opacity-0' : style.text)}>
                  {line.continued ? '' : style.label}
                </span>
                <span className={cn(
                  'min-w-0 flex-1 whitespace-pre-wrap break-words',
                  line.level === 'ERROR' ? 'text-destructive' : 'text-foreground/90',
                )}>
                  {line.logger ? <span className="mr-2 text-muted-foreground">{line.logger}</span> : null}
                  {highlight(line.message, shownQuery)}
                </span>
              </div>
            );
          }) : (
            <p className="px-4 py-10 text-center font-sans text-sm text-muted-foreground">
              {shownQuery ? `没有包含「${shownQuery}」的日志` : filter === 'all' ? '日志为空' : '最近没有这一级别的日志'}
            </p>
          )}
        </div>
        {!atBottom ? (
          <Button size="sm" onClick={toBottom} className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full shadow-md">
            <ArrowDown />回到最新
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <Page
      title="运行日志"
      description="监听、识别、转存和通知的运行记录，用来排查问题。"
      actions={(
        <>
          <Button variant="outline" className="bg-card" onClick={() => { if (!auto) reload(); setAuto(!auto); }} aria-pressed={auto}>
            {auto ? <Pause /> : <Play />}
            {auto ? '暂停刷新' : '自动刷新'}
          </Button>
          <Button variant="outline" className="bg-card" disabled={logs.loading} onClick={reload}>
            <RefreshCw className={cn(logs.loading && 'animate-spin')} />刷新
          </Button>
          <Button variant="outline" className="bg-card hover:border-destructive/40 hover:bg-destructive/5 hover:text-destructive"
            disabled={clearing || missing} onClick={() => void clear()}>
            <Trash2 />清空日志
          </Button>
        </>
      )}
    >
      <Panel bodyClassName="p-0">
        <div className="flex flex-col gap-2.5 border-b border-border/70 p-3 sm:flex-row sm:items-center sm:p-4">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索关键字，如：失败、115、审批"
              aria-label="搜索日志"
              className="bg-card pl-9 pr-9"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="清空搜索"
                className="absolute right-1 top-1/2 grid size-9 -translate-y-1/2 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground sm:right-1.5 sm:size-7"
              >
                <X className="size-3.5" />
              </button>
            ) : null}
          </div>
          <Segmented<LevelFilter>
            label="日志级别" value={filter} onChange={setFilter} className="shrink-0 self-start sm:self-auto"
            options={filters.map((f) => ({ value: f.value, label: <>{f.label}<SegmentCount count={f.count} tone={f.tone} /></> }))}
          />
        </div>

        {error && lines !== null ? (
          <div role="alert" className="flex items-center gap-2 border-b border-destructive/20 bg-destructive/5 px-4 py-2 text-xs text-destructive">
            <CircleAlert className="size-3.5 shrink-0" />刷新失败：{error}
          </div>
        ) : null}

        {body}

        {lines !== null && !missing ? (
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-border/70 px-4 py-2.5 text-xs text-muted-foreground">
            <span className="flex items-center gap-2">
              <span className={cn('size-1.5 rounded-full', auto ? 'animate-pulse bg-success' : 'bg-muted-foreground/50')} />
              {auto ? `每 ${REFRESH_MS / 1000} 秒自动刷新` : '已暂停自动刷新'} · 最近 {FETCH_LIMIT} 行内显示 {visible.length} 行
            </span>
            {path ? <span className="min-w-0 truncate font-mono text-[11px]" title={path}>{path}</span> : null}
          </div>
        ) : null}
      </Panel>
    </Page>
  );
}
