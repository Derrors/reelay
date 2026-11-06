/** 版本更新：当前版本、新版本的更新说明与升级方法。源码部署可以在页面内确认后一键升级；Docker 部署只提示，
 *  由用户拉取新镜像。不用弹窗：新版本的提醒在右上角提示一次，详情都在这一页。 */
import { useState } from 'react';
import {
  ArrowUpCircle, CircleAlert, CircleCheck, CircleX, Copy, ExternalLink, Info, Loader2, PackageCheck, RefreshCw, Terminal,
} from 'lucide-react';
import { toast } from 'sonner';
import { isUpgrading, updateApi } from '@/api/update';
import { Notice } from '@/components/app/Notice';
import { Page } from '@/components/app/Page';
import { Panel } from '@/components/app/Panel';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useUpdate } from '@/hooks/useUpdateStatus';
import { relativeTime } from '@/lib/format';
import type { IUpdateStatus, UpgradeStep } from '@/lib/types';
import { copyText, errorMessage } from '@/lib/utils';

export const UPGRADE_COMMAND = 'docker compose pull && docker compose up -d';

const STEP_LABELS: Record<UpgradeStep, string> = {
  fetch: '拉取新版本', checkout: '切换代码', pip: '更新 Python 依赖', npm: '构建管理台', restart: '重启主进程与管理服务',
};

/** 更新说明（GitHub Release 的 Markdown）：标题加粗、列表加圆点，其余按原文显示，不解析 HTML。 */
function ReleaseNotes({ text }: { text: string }) {
  const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
  return (
    <div className="space-y-1 text-[13px] leading-6">
      {lines.map((line, i) => {
        const heading = /^#{1,6}\s+(.*)$/.exec(line);
        if (heading) return <p key={i} className="pt-1 font-medium first:pt-0">{heading[1]}</p>;
        const item = /^[-*]\s+(.*)$/.exec(line);
        if (item) return <p key={i} className="flex gap-2 text-foreground/85"><span className="text-muted-foreground">•</span><span className="min-w-0 break-words">{item[1]}</span></p>;
        return <p key={i} className="break-words text-foreground/85">{line}</p>;
      })}
    </div>
  );
}

/** 源码部署：页面内两步确认后一键升级，显示进度或失败原因；不满足条件时给出原因与手动升级的步骤。 */
function SourceUpgrade({ status, onChange }: { status: IUpdateStatus; onChange: (status: IUpdateStatus) => void }) {
  const { supported, reason, state } = status.upgrade;
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const start = async () => {
    setStarting(true);
    try {
      onChange(await updateApi.apply());
    } catch (e) {
      toast.error(errorMessage(e, '升级没有开始'));
    } finally {
      setStarting(false);
      setConfirming(false);
    }
  };

  if (state && isUpgrading(status)) {
    return (
      <Notice tone="info" icon={Loader2} title={`正在${STEP_LABELS[state.step]}（v${state.from} → v${state.target}）`}>
        完成后主进程与管理服务会自动重启，页面随后自动刷新；期间管理台会断开一会儿，可以先去做别的。
      </Notice>
    );
  }
  return (
    <div className="space-y-3">
      {state?.status === 'failed' ? (
        <Notice tone="danger" icon={CircleX} title={`升级到 v${state.target} 失败`}>
          <p>{state.error}</p>
          {state.log ? (
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-muted-foreground">升级日志（data/upgrade.log 末尾）</summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/60 p-2 font-mono text-[11px] leading-5 text-foreground/80">{state.log}</pre>
            </details>
          ) : null}
        </Notice>
      ) : null}
      {supported ? (
        <>
          <p className="text-[13px] leading-6 text-muted-foreground">
            {`一键升级会拉取 v${status.latest} 的代码，依赖或管理台有变化时一并更新，然后重启主进程与管理服务；`
              + '进行中的转存在重启后继续，出错时代码退回当前版本。升级前建议先备份 data 目录。'}
          </p>
          {confirming ? (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/25 bg-primary/5 px-3 py-2.5">
              <span className="flex-1 text-[13px]">确认从 v{status.current} 升级到 v{status.latest}？</span>
              <Button size="sm" variant="ghost" disabled={starting} onClick={() => setConfirming(false)}>取消</Button>
              <Button size="sm" disabled={starting} onClick={() => void start()}>
                {starting ? <Loader2 className="animate-spin" /> : <ArrowUpCircle />}确认升级
              </Button>
            </div>
          ) : (
            <Button onClick={() => setConfirming(true)}><ArrowUpCircle />立即升级</Button>
          )}
        </>
      ) : (
        <Notice tone="info" icon={Info} title={`不能一键升级：${reason}`}>
          手动升级：在项目目录执行 git pull；依赖有变化时执行 pip install -c constraints.txt -e ".[all]"；管理台有变化时执行
          npm ci --prefix web && npm run build --prefix web；最后重启主进程与管理服务。
        </Notice>
      )}
    </div>
  );
}

/** Docker 部署：只提示，升级由用户在服务器上拉取新镜像。 */
function DockerUpgrade({ status }: { status: IUpdateStatus }) {
  const copy = () => {
    copyText(UPGRADE_COMMAND).then(() => toast.success('已复制升级命令'), () => toast.error('复制失败，请手动选择命令'));
  };
  return (
    <div className="space-y-2">
      {status.pinned ? (
        <Notice tone="warning" icon={CircleAlert}>
          .env 里固定了 REELAY_VERSION={status.pinned}，直接拉取不会升级；先把它改成 {status.latest}（或删掉这一行使用 latest）。
        </Notice>
      ) : null}
      <p className="text-[13px] leading-6 text-muted-foreground">在服务器的部署目录（compose.yaml 所在目录）执行，升级前建议先备份数据卷：</p>
      <div className="flex items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2">
        <code className="min-w-0 flex-1 select-all break-all font-mono text-xs">{UPGRADE_COMMAND}</code>
        <Button size="icon" variant="ghost" className="size-8 shrink-0" onClick={copy} aria-label="复制升级命令">
          <Copy className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

export default function UpdatePage() {
  const { data: status, mutate } = useUpdate();
  const [checking, setChecking] = useState(false);
  const replace = (next: IUpdateStatus) => mutate(() => next);
  const check = async () => {
    setChecking(true);
    try {
      const result = await updateApi.check();
      replace(result);
      if (!result.error && !result.available && result.enabled) toast.success('已是最新版本');
    } catch (e) {
      toast.error(errorMessage(e, '检查更新失败'));
    } finally {
      setChecking(false);
    }
  };
  const busy = isUpgrading(status);
  const description = status
    ? `当前版本 v${status.current}${status.checked_at ? ` · ${relativeTime(status.checked_at)}检查` : ''}`
    : '当前版本与 GitHub 上的最新正式版';

  return (
    <Page title="版本更新" description={description}
      actions={(
        <Button variant="outline" disabled={checking || busy || !status?.enabled} onClick={() => void check()}>
          {checking ? <Loader2 className="animate-spin" /> : <RefreshCw />}重新检查
        </Button>
      )}>
      {!status ? (
        <Skeleton className="h-40 w-full rounded-xl" />
      ) : !status.enabled ? (
        <Notice tone="info" icon={Info}>版本更新检查已在「插件」页关闭，开启后才会查询新版本。</Notice>
      ) : (
        <div className="space-y-5">
          {status.error ? <Notice tone="warning" icon={CircleAlert}>{status.error}</Notice> : null}
          {status.available || busy ? (
            <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
              <Panel icon={PackageCheck} title={`Reelay v${status.latest} 更新说明`}
                actions={(
                  <a href={status.url} target="_blank" rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
                    GitHub<ExternalLink className="size-3.5" />
                  </a>
                )} bodyClassName="p-4 sm:p-5">
                {status.notes ? <ReleaseNotes text={status.notes} /> : <p className="text-sm text-muted-foreground">这个版本没有写更新说明。</p>}
              </Panel>
              <Panel icon={Terminal} title="升级" description={status.docker ? 'Docker 部署：拉取新镜像升级' : '源码部署'}
                bodyClassName="p-4 sm:p-5">
                {status.docker ? <DockerUpgrade status={status} /> : <SourceUpgrade status={status} onChange={replace} />}
              </Panel>
            </div>
          ) : status.latest ? (
            <Notice tone="success" icon={CircleCheck}>已是最新版本（GitHub 最新正式版 v{status.latest}）。</Notice>
          ) : null}
        </div>
      )}
    </Page>
  );
}
