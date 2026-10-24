import { useEffect, useRef, type ReactNode } from 'react';
import { CircleAlert, Loader2, RefreshCw, RotateCcw, Save } from 'lucide-react';
import { Notice } from '@/components/app/Notice';
import { Page } from '@/components/app/Page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import type { ConfigDraft } from './useConfigDraft';

function LoadingState() {
  return (
    <div className="space-y-5" role="status" aria-label="正在加载设置">
      <Skeleton className="h-9 w-full rounded-lg sm:w-96" />
      <Skeleton className="h-56 rounded-xl" />
      <div className="grid gap-5 xl:grid-cols-2">
        <Skeleton className="h-80 rounded-xl" />
        <Skeleton className="h-80 rounded-xl" />
      </div>
    </div>
  );
}

/** 设置页与插件页的外壳：加载与错误提示、「重新加载」，以及有未保存修改时贴底的保存条。 */
export function ConfigFrame({ draft, title, description, dirtyLabels, onSave, children }: {
  draft: ConfigDraft; title: string; description: ReactNode;
  /** 有改动的分区名，显示在保存条上。 */
  dirtyLabels: string[];
  onSave: () => void;
  children: ReactNode;
}) {
  const { data, dirty, busy, loadError, errors } = draft;
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (errors.length) errorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [errors]);

  return (
    <Page
      title={title}
      description={description}
      actions={data ? (
        <>
          {!dirty ? (
            <span className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
              <span className="size-1.5 rounded-full bg-success" />所有设置均已生效
            </span>
          ) : null}
          <Button
            variant="outline" size="sm" disabled={busy || dirty}
            title={dirty ? '请先保存或放弃修改' : undefined} onClick={() => void draft.reload()}
          >
            <RefreshCw />重新加载
          </Button>
        </>
      ) : undefined}
    >
      {loadError ? (
        <Notice tone="danger" icon={CircleAlert} title="设置加载失败" className="mb-5"
          action={<Button size="sm" variant="outline" className="bg-card" onClick={() => void draft.reload()}>重试</Button>}>
          {loadError}
        </Notice>
      ) : null}
      {!data && !loadError ? <LoadingState /> : null}

      {data ? (
        <div className="space-y-5">
          {data.config_error ? (
            <Notice tone="danger" icon={CircleAlert} title="配置文件存在错误，保存已暂停">
              {data.config_error}。页面显示的是最后一份可用配置，修复配置文件后点「重新加载」。
            </Notice>
          ) : null}
          {errors.length ? (
            <div ref={errorRef}>
              <Notice tone="danger" icon={CircleAlert} title="设置还没有保存，请先处理以下问题">
                <ul className="list-disc space-y-0.5 pl-4">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
              </Notice>
            </div>
          ) : null}
          {children}
        </div>
      ) : null}

      {data && dirty ? (
        <div className="sticky bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-20 mt-6 lg:bottom-5">
          <div className="flex items-center gap-3 rounded-xl border border-warning/35 bg-card/95 px-3.5 py-2.5 shadow-lg backdrop-blur animate-in fade-in-0 slide-in-from-bottom-2 sm:px-4 sm:py-3">
            <span className="size-2 shrink-0 rounded-full bg-warning" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-5">有修改尚未保存</p>
              <p className="truncate text-xs leading-5 text-muted-foreground">{dirtyLabels.join('、') || '设置'}</p>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button variant="outline" size="sm" disabled={busy} onClick={() => draft.restore(draft.dirtyKeys)}>
                <RotateCcw /><span className="sm:hidden">放弃</span><span className="hidden sm:inline">放弃修改</span>
              </Button>
              <Button size="sm" disabled={busy || Boolean(data.config_error)} onClick={onSave}>
                {busy ? <Loader2 className="animate-spin" /> : <Save />}
                <span className="sm:hidden">保存</span><span className="hidden sm:inline">保存并生效</span>
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </Page>
  );
}
