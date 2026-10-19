import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircle2, ChevronDown, CircleDashed, Cloud, FolderCog, KeyRound, Link2,
  Loader2, QrCode, RefreshCw, ScanLine, ShieldAlert, ShieldCheck, Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { extras, type IAuth115Status } from '@/api/extras';

const stateText: Record<string, string> = {
  active: '连接正常',
  expired: '凭证失效',
  error: '连接异常',
  unknown: '等待检测',
};

type QrMode = 'cookie' | 'oauth';

function StateTag({ state, configured }: { state: string; configured: boolean }) {
  if (!configured) {
    return <span className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-2.5 py-1 text-xs text-muted-foreground">
      <CircleDashed className="size-3.5" />未配置
    </span>;
  }
  const ok = state === 'active';
  const bad = state === 'expired' || state === 'error';
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
      ok ? 'border-emerald-600/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
        : bad ? 'border-destructive/25 bg-destructive/10 text-destructive'
          : 'border-amber-600/25 bg-amber-500/10 text-amber-700 dark:text-amber-300'
    }`}>
      {ok ? <CheckCircle2 className="size-3.5" /> : bad ? <ShieldAlert className="size-3.5" /> : <CircleDashed className="size-3.5" />}
      {stateText[state] ?? state}
    </span>
  );
}

function ChannelCard({
  icon, title, eyebrow, state, configured, description, detail, danger, children,
}: {
  icon: React.ReactNode;
  title: string;
  eyebrow: string;
  state: string;
  configured: boolean;
  description: string;
  detail: string;
  danger?: string;
  children?: React.ReactNode;
}) {
  return (
    <section className="min-w-0 rounded-lg border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">{icon}</div>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-[.14em] text-muted-foreground">{eyebrow}</p>
            <h3 className="mt-0.5 truncate text-sm font-semibold text-foreground sm:text-base">{title}</h3>
          </div>
        </div>
        <StateTag state={state} configured={configured} />
      </div>
      <p className="mt-4 text-sm leading-6 text-foreground">{description}</p>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">{danger || detail}</p>
      {children}
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
      toast.success('双通道检测完成');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '探活失败');
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
      setQrMode(null); setQrUrl(null); setQrUid(null);
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

  return (
    <Card className="overflow-hidden rounded-lg shadow-none">
      <div className="border-b bg-muted/20 px-5 py-6 sm:px-7">
        <div className="hidden" />
        <div className="hidden" />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-primary">
              <ShieldCheck className="size-5" />
              <span className="text-xs font-semibold uppercase tracking-[.18em]">115 账号连接</span>
            </div>
            <h2 className="mt-3 text-2xl font-bold tracking-tight">连接你的 115 网盘</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              两种登录方式配合使用，扫码即可自动完成：<b className="text-foreground/80">扫码登录</b>负责接收他人分享的资源，<b className="text-foreground/80">应用授权</b>负责转存后的改名与整理。
            </p>
          </div>
          <div className="rounded-lg border bg-background px-4 py-3 text-right">
            <p className="text-[11px] uppercase tracking-[.16em] text-muted-foreground">已连接</p>
            <p className="mt-1 text-xl font-bold">{readyCount}<span className="ml-1 text-sm font-normal text-muted-foreground">/ 2 种方式</span></p>
          </div>
        </div>
      </div>

      <CardContent className="space-y-5 p-5 sm:p-7">
        {statusError && (
          <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            {statusError}
            <Button size="sm" variant="ghost" className="ml-2 text-destructive" onClick={() => void loadStatus()}>重新加载</Button>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-[1fr_auto_1fr] lg:items-stretch">
          <ChannelCard
            icon={<Link2 className="size-5" />}
            eyebrow="方式一 · 接收分享必备"
            title="扫码登录 115"
            state={status?.cookie.state ?? 'unknown'}
            configured={Boolean(status?.cookie.configured)}
            description="相当于在网页上登录 115，用来浏览并接收别人分享给你的资源，是影视转存必须完成的一步。"
            detail={status?.cookie.configured ? '登录态会自动维护，过期时重新扫码即可' : '未登录时无法接收分享链接。'}
            danger={status?.cookie.error}
          />
          <div className="hidden items-center justify-center lg:flex">
            <div className="grid size-9 place-items-center rounded-full border bg-muted text-muted-foreground">+</div>
          </div>
          <ChannelCard
            icon={<FolderCog className="size-5" />}
            eyebrow="方式二 · 改名整理"
            title="授权文件管理"
            state={status?.oauth.state ?? 'unknown'}
            configured={Boolean(status?.oauth.configured)}
            description="授权后才能在网盘里建文件夹、改名、移动文件；授权到期前会自动续期，无需反复操作。"
            detail={status?.oauth.configured ? '已授权，转存后的整理操作会自动完成' : '暂未授权时，改名整理会临时改用方式一执行。'}
            danger={status?.oauth.error}
          >
            {status?.oauth.configured && (
              <Button size="sm" variant="ghost" className="mt-2 h-7 px-2 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive" onClick={() => void clearOauth()}>
                <Trash2 className="size-3.5" />取消授权
              </Button>
            )}
          </ChannelCard>
        </div>

        {status?.traffic?.cooling_down && (
          <div className="rounded-lg border border-amber-600/30 bg-amber-500/10 p-4 text-sm text-amber-800 dark:text-amber-200">
            115 暂时限制了操作频率，约 {status.traffic.cooldown_remaining ?? 0} 秒后自动恢复。期间系统会自动等待，不会反复重试，请放心。
          </div>
        )}

        <section className="rounded-lg border bg-muted/20 p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[.16em] text-primary">操作</p>
              <h3 className="mt-1 text-base font-semibold">扫码连接网盘</h3>
            </div>
            <p className="text-xs text-muted-foreground">建议先扫码登录，再做应用授权</p>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <Button variant="outline" className="h-auto min-h-20 flex-col gap-2 rounded-lg py-4"
                    disabled={Boolean(qrMode) || qrBusy !== null} onClick={() => void startQr('cookie')}>
              {qrBusy === 'cookie' ? <Loader2 className="size-5 animate-spin" /> : <ScanLine className="size-5" />}
              <span>{qrMode === 'cookie' ? '等待你扫码确认' : '扫码登录 115'}</span>
            </Button>
            <Button variant="outline" className="h-auto min-h-20 flex-col gap-2 rounded-lg py-4"
                    disabled={Boolean(qrMode) || qrBusy !== null} onClick={() => void startQr('oauth')}>
              {qrBusy === 'oauth' ? <Loader2 className="size-5 animate-spin" /> : <QrCode className="size-5" />}
              <span>{qrMode === 'oauth' ? '等待你扫码确认' : '授权文件管理'}</span>
            </Button>
            <Button variant="outline" className="h-12 rounded-lg"
                    disabled={pinging} onClick={() => void ping()}>
              {pinging ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}检测连接是否正常
            </Button>
            <Button variant="outline" className="h-12 rounded-lg"
                    onClick={() => void loadStatus()}>
              <Cloud className="size-4" />刷新状态
            </Button>
          </div>

          {qrUrl && qrUid && qrMode && (
            <div className="mt-5 grid gap-4 rounded-lg border bg-background p-4 sm:grid-cols-[184px_1fr] sm:items-center">
              <img src={qrImage} alt={qrMode === 'cookie' ? '115 登录二维码' : '115 授权二维码'}
                   className="mx-auto size-44 rounded-xl bg-white p-2 shadow-xl" />
              <div>
                <p className="text-xs font-semibold uppercase tracking-[.16em] text-primary">请用手机 115 App 扫码</p>
                <h4 className="mt-2 text-lg font-semibold">{qrMode === 'cookie' ? '登录并接收分享' : '授权文件整理能力'}</h4>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">{qrMsg}</p>
                <p className="mt-3 text-xs leading-5 text-muted-foreground">直接用 App 扫码即可，不要保存或转发二维码图片。扫码登录使用独立会话，不会影响你手机和网页上已登录的 115。</p>
              </div>
            </div>
          )}
        </section>

        <details className="group rounded-lg border bg-card">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-4 text-sm font-medium text-foreground">
            <span className="flex items-center gap-2"><KeyRound className="size-4 text-muted-foreground" />无法扫码？手动粘贴登录信息（高级）</span>
            <ChevronDown className="size-4 text-muted-foreground transition group-open:rotate-180" />
          </summary>
          <div className="border-t p-4">
            <p className="mb-2 text-xs leading-5 text-muted-foreground">仅推荐有经验的用户在扫码失败时使用。从浏览器登录 115 后复制完整 Cookie 粘贴进来，保存后会自动检测是否有效。</p>
            <Textarea value={cookieText} onChange={(e) => setCookieText(e.target.value)}
                      placeholder="UID=...; CID=...; SEID=...; KID=..." spellCheck={false}
                      className="min-h-24 font-mono text-xs" />
            <div className="mt-3 flex justify-end">
              <Button size="sm" disabled={savingCookie} onClick={() => void saveCookie()}>
                {savingCookie ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />}保存并验证
              </Button>
            </div>
          </div>
        </details>
      </CardContent>
    </Card>
  );
}
