import { useCallback, useEffect, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  AlertTriangle, ChevronDown, CircleAlert, FolderCog, KeyRound, Link2, Loader2, QrCode, RefreshCw,
  ScanLine, ShieldCheck, Trash2, X,
} from 'lucide-react';
import { toast } from 'sonner';
import { Panel } from '@/components/app/Panel';
import { ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { extras, type IAuth115Side, type IAuth115Status } from '@/api/extras';
import { fullTime, relativeTime } from '@/lib/format';
import type { Tone } from '@/lib/status';

type QrMode = 'cookie' | 'oauth';

const STATE_META: Record<string, { label: string; tone: Tone }> = {
  active: { label: '连接正常', tone: 'success' },
  expired: { label: '凭证失效', tone: 'danger' },
  error: { label: '连接异常', tone: 'danger' },
  unknown: { label: '等待检测', tone: 'info' },
};

function SideBadge({ side }: { side?: IAuth115Side }) {
  if (!side?.configured) return <ToneBadge tone="neutral">未配置</ToneBadge>;
  const meta = STATE_META[side.state] ?? STATE_META.unknown;
  return <ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>;
}

function MethodCard({ icon: Icon, step, title, side, description, detail, children }: {
  icon: LucideIcon; step: string; title: string; side?: IAuth115Side;
  description: string; detail: string; children: React.ReactNode;
}) {
  return (
    <section className="flex min-w-0 flex-col rounded-lg border border-border bg-background/60 p-4">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Icon className="size-4" /></span>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">{step}</p>
          <h3 className="text-sm font-semibold leading-6">{title}</h3>
        </div>
        <SideBadge side={side} />
      </div>
      <p className="mt-3 text-[13px] leading-6 text-foreground/85">{description}</p>
      {side?.error ? (
        <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-destructive">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" />{side.error}
        </p>
      ) : (
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {detail}
          {side?.checked_at ? <span title={fullTime(side.checked_at)}> · 上次检测 {relativeTime(side.checked_at)}</span> : null}
        </p>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-2 pt-4">{children}</div>
    </section>
  );
}

export default function Auth115Card() {
  const [status, setStatus] = useState<IAuth115Status | null>(null);
  const [statusError, setStatusError] = useState('');
  const [cookieText, setCookieText] = useState('');
  const [savingCookie, setSavingCookie] = useState(false);
  const [pinging, setPinging] = useState(false);
  const [qrMode, setQrMode] = useState<QrMode | null>(null);
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const [qrUid, setQrUid] = useState<string | null>(null);
  const [qrMsg, setQrMsg] = useState('');
  const [qrBusy, setQrBusy] = useState<QrMode | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      setStatusError('');
      setStatus(await extras.auth115Status());
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : '读取 115 连接状态失败');
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadStatus(), 0);
    return () => window.clearTimeout(timer);
  }, [loadStatus]);

  const ping = async () => {
    setPinging(true);
    try {
      const r = await extras.auth115Ping();
      setStatus(r.status);
      toast.success('检测完成');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '检测失败');
      await loadStatus();
    } finally {
      setPinging(false);
    }
  };

  const saveCookie = async () => {
    const cookies = cookieText.trim();
    if (!cookies) { toast.error('请先粘贴登录信息'); return; }
    setSavingCookie(true);
    try {
      const r = await extras.auth115SetCookie(cookies);
      toast.success(r.state === 'active' ? '登录信息已保存，检测通过' : '登录信息已保存');
      setCookieText('');
      await loadStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '登录信息保存失败');
    } finally {
      setSavingCookie(false);
    }
  };

  const stopQr = () => { setQrMode(null); setQrUrl(null); setQrUid(null); };

  const startQr = async (mode: QrMode) => {
    setQrBusy(mode);
    setQrMsg('');
    try {
      const info = mode === 'cookie' ? await extras.auth115CookieQrStart() : await extras.auth115QrStart();
      setQrMode(mode);
      setQrUrl(info.qrcode_url);
      setQrUid(info.uid);
      setQrMsg(mode === 'cookie'
        ? '请在 115 App 中确认登录，完成后会自动保存'
        : '请在 115 App 中确认授权文件管理');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '申请授权二维码失败');
      stopQr();
    } finally {
      setQrBusy(null);
    }
  };

  useEffect(() => {
    if (!qrUid || !qrUrl || !qrMode) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = qrMode === 'cookie'
          ? await extras.auth115CookieQrStatus(qrUid)
          : await extras.auth115QrStatus(qrUid);
        if (cancelled) return;
        setQrMsg(r.message);
        if (r.authenticated) {
          const label = qrMode === 'cookie' ? '115 登录成功' : '文件管理授权成功';
          setQrMode(null); setQrUrl(null); setQrUid(null);
          toast.success(label);
          await loadStatus();
          return;
        }
        if (r.status === -1 || r.status === -2) {
          setQrMode(null); setQrUrl(null); setQrUid(null);
          return;
        }
        timeout = setTimeout(() => void poll(), 2500);
      } catch (err) {
        if (cancelled) return;
        setQrMsg(err instanceof Error ? err.message : '查询扫码状态失败');
        setQrMode(null); setQrUrl(null); setQrUid(null);
      }
    };
    timeout = setTimeout(() => void poll(), 1800);
    return () => { cancelled = true; clearTimeout(timeout); };
  }, [qrMode, qrUid, qrUrl, loadStatus]);

  const clearOauth = async () => {
    if (!window.confirm('取消后，转存后的改名整理会改用扫码登录执行。确定取消文件管理授权吗？')) return;
    try {
      await extras.auth115ClearOauth();
      toast.success('已取消文件管理授权');
      await loadStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '取消授权失败');
    }
  };

  const readyCount = Number(Boolean(status?.cookie.configured)) + Number(Boolean(status?.oauth.configured));
  const qrImage = qrMode === 'cookie' && qrUid
    ? extras.auth115CookieQrImageUrl(qrUid)
    : qrUid ? extras.auth115QrImageUrl(qrUid) : '';
  const scanDisabled = Boolean(qrMode) || qrBusy !== null;
  const scanButton = (mode: QrMode, configured: boolean, label: string, again: string) => (
    <Button size="sm" variant={configured ? 'outline' : 'default'} disabled={scanDisabled} onClick={() => void startQr(mode)}>
      {qrBusy === mode ? <Loader2 className="animate-spin" /> : mode === 'cookie' ? <ScanLine /> : <QrCode />}
      {qrMode === mode ? '等待扫码…' : configured ? again : label}
    </Button>
  );

  return (
    <Panel
      icon={ShieldCheck}
      title="115 网盘连接"
      description="两种方式配合使用：扫码登录负责接收他人分享的资源，文件管理授权负责转存后的改名与整理。"
      actions={status ? (
        <ToneBadge tone={readyCount === 2 ? 'success' : readyCount ? 'warning' : 'neutral'} className="tabular-nums">
          已连接 {readyCount}/2
        </ToneBadge>
      ) : undefined}
      bodyClassName="space-y-4 p-4 sm:p-5"
    >
      {statusError ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3.5 py-2.5 text-sm text-destructive">
          <CircleAlert className="size-4 shrink-0" />
          <span className="min-w-0 flex-1">{statusError}</span>
          <Button size="sm" variant="outline" className="bg-card" onClick={() => void loadStatus()}>重新加载</Button>
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <MethodCard
          icon={Link2}
          step="第 1 步 · 接收分享必备"
          title="扫码登录 115"
          side={status?.cookie}
          description="相当于在网页上登录 115，用来浏览并接收别人分享的资源，是转存必须完成的一步。"
          detail={status?.cookie.configured ? '登录状态会自动维护，过期时重新扫码即可。' : '未登录时无法接收分享链接。'}
        >
          {scanButton('cookie', Boolean(status?.cookie.configured), '扫码登录', '重新扫码')}
        </MethodCard>
        <MethodCard
          icon={FolderCog}
          step="第 2 步 · 改名整理"
          title="授权文件管理"
          side={status?.oauth}
          description="授权后才能在网盘里建文件夹、改名、移动文件；到期前会自动续期，无需反复操作。"
          detail={status?.oauth.configured ? '已授权，转存后的整理会自动完成。' : '暂未授权时，改名整理会临时改用扫码登录执行。'}
        >
          {scanButton('oauth', Boolean(status?.oauth.configured), '扫码授权', '重新授权')}
          {status?.oauth.configured ? (
            <Button size="sm" variant="ghost" className="text-destructive hover:bg-destructive/10 hover:text-destructive" onClick={() => void clearOauth()}>
              <Trash2 />取消授权
            </Button>
          ) : null}
        </MethodCard>
      </div>

      {qrUrl && qrUid && qrMode ? (
        <div className="flex flex-col items-center gap-4 rounded-lg border border-primary/25 bg-primary/[0.04] p-4 sm:flex-row sm:items-start sm:gap-5">
          {/* 二维码需要深色码点配浅色底才能被识别，深色模式下也保持白底 */}
          <img
            src={qrImage} alt={qrMode === 'cookie' ? '115 登录二维码' : '115 授权二维码'}
            className="size-44 shrink-0 rounded-lg bg-white p-2 shadow-md ring-1 ring-black/5"
          />
          <div className="min-w-0 flex-1 text-center sm:text-left">
            <p className="text-xs font-medium text-primary">请用手机 115 App 扫码</p>
            <h4 className="mt-1 text-base font-semibold">{qrMode === 'cookie' ? '登录并接收分享' : '授权文件整理'}</h4>
            <p className="mt-2 flex items-center justify-center gap-2 text-sm text-foreground/85 sm:justify-start">
              <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />{qrMsg}
            </p>
            <p className="mt-3 text-xs leading-5 text-muted-foreground">
              直接用 App 扫码即可，不要保存或转发二维码图片。{qrMode === 'cookie'
                ? '扫码登录使用独立会话，不会影响手机和网页上已登录的 115。'
                : '授权后 Reelay 通过 115 开放平台整理文件，可随时在这里取消授权。'}
            </p>
            <Button size="sm" variant="ghost" className="mt-3" onClick={stopQr}><X />取消</Button>
          </div>
        </div>
      ) : null}

      {status?.traffic?.cooling_down ? (
        <div className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning/12 px-3.5 py-2.5 text-[13px] leading-6">
          <AlertTriangle className="mt-1 size-4 shrink-0 text-warning" />
          <span>115 暂时限制了操作频率，约 <b className="tabular-nums">{status.traffic.cooldown_remaining ?? 0}</b> 秒后自动恢复。期间系统会自动等待，不会反复重试。</span>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 border-t border-border/70 pt-4">
        <Button size="sm" variant="outline" disabled={pinging} onClick={() => void ping()}>
          {pinging ? <Loader2 className="animate-spin" /> : <RefreshCw />}检测连接是否正常
        </Button>
        <Button size="sm" variant="ghost" onClick={() => void loadStatus()}>刷新状态</Button>
        <span className="text-xs text-muted-foreground sm:ml-auto">建议先扫码登录，再授权文件管理</span>
      </div>

      <details className="group rounded-lg border border-border bg-background/60">
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 py-2.5 text-[13px] font-medium [&::-webkit-details-marker]:hidden">
          <span className="flex items-center gap-2"><KeyRound className="size-4 text-muted-foreground" />无法扫码？手动粘贴登录信息（高级）</span>
          <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
        </summary>
        <div className="space-y-3 border-t border-border/70 p-4">
          <p className="text-xs leading-5 text-muted-foreground">仅在扫码失败时使用：在浏览器登录 115 后复制完整 Cookie 粘贴进来，保存后会自动检测是否有效。</p>
          <Textarea
            value={cookieText} onChange={(e) => setCookieText(e.target.value)}
            placeholder="UID=...; CID=...; SEID=...; KID=..." spellCheck={false}
            className="min-h-24 break-all bg-card font-mono text-xs md:text-xs"
          />
          <div className="flex justify-end">
            <Button size="sm" disabled={savingCookie} onClick={() => void saveCookie()}>
              {savingCookie ? <Loader2 className="animate-spin" /> : <KeyRound />}保存并验证
            </Button>
          </div>
        </div>
      </details>
    </Panel>
  );
}
