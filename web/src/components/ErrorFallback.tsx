import { RefreshCw, RotateCcw, TriangleAlert } from 'lucide-react';
import type { FallbackProps } from 'react-error-boundary';
import { Button } from '@/components/ui/button';

export function ErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex min-h-svh flex-col items-center justify-center px-5 py-10 text-center">
      <span className="grid size-12 place-items-center rounded-full bg-destructive/10 text-destructive">
        <TriangleAlert className="size-6" />
      </span>
      <h1 className="mt-5 text-[22px] font-semibold tracking-tight">页面出错了</h1>
      <p className="mt-1.5 max-w-sm text-sm leading-6 text-muted-foreground">
        界面遇到了意外错误，后台任务不受影响。可以先重试；如果反复出现，请刷新页面。
      </p>
      {message ? (
        <details className="mt-5 w-full max-w-md rounded-lg border border-border bg-card text-left">
          <summary className="cursor-pointer select-none px-4 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground">
            错误详情
          </summary>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words border-t border-border px-4 py-3 font-mono text-xs leading-5 text-foreground/80">
            {message}
          </pre>
        </details>
      ) : null}
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Button onClick={resetErrorBoundary}><RotateCcw />重试</Button>
        <Button variant="outline" className="bg-card" onClick={() => window.location.reload()}><RefreshCw />刷新页面</Button>
      </div>
    </div>
  );
}
