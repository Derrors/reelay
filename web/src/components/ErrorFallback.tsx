import type { FallbackProps } from 'react-error-boundary';

export function ErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-8 text-center">
      <h1 className="text-xl font-semibold">页面出错了</h1>
      <pre className="max-w-full overflow-auto rounded-md bg-muted p-4 text-left text-sm text-muted-foreground">
        {error instanceof Error ? error.message : String(error)}
      </pre>
      <button
        type="button"
        className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
        onClick={resetErrorBoundary}
      >
        重试
      </button>
    </div>
  );
}
