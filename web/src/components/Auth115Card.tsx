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
    return <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-slate-300">
      <CircleDashed className="size-3.5" />未配置
    </span>;
  }
  const ok = state === 'active';
  const bad = state === 'expired' || state === 'error';
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
      ok ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'
        : bad ? 'border-rose-400/30 bg-rose-400/10 text-rose-300'
          : 'border-amber-300/20 bg-amber-300/10 text-amber-200'
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
    <section className="min-w-0 rounded-2xl border border-white/10 bg-white/[0.055] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,.06)] sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-fuchsia-400/10 text-fuchsia-200">{icon}</div>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-[.16em] text-slate-400">{eyebrow}</p>
            <h3 className="mt-0.5 truncate text-sm font-semibold text-white sm:text-base">{title}</h3>
          </div>
        </div>
        <StateTag state={state} configured={configured} />
      </div>
      <p className="mt-4 text-sm leading-6 text-slate-200">{description}</p>
      <p className="mt-2 text-xs leading-5 text-slate-400">{danger || detail}</p>
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
      setStatusError(err instanceof Error ? err.message : '读取 115 鉴权状态失败');
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
    if (!cookies) { toast.error('请先粘贴 Cookie'); return; }
    setSavingCookie(true);
    try {
      const r = await extras.auth115SetCookie(cookies);
      toast.success(r.state === 'active' ? 'Cookie 已保存并验证通过' : 'Cookie 已保存');
      setCookieText('');
      await loadStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存 Cookie 失败');
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
        ? '用 115 App 扫码并确认，完成后 Cookie 会自动保存'
        : '用 115 App 扫码并确认开放平台授权');
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
          const label = qrMode === 'cookie' ? 'Cookie 登录成功' : '开放平台授权成功';
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
      toast.success('已清除开放平台授权');
      await loadStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '清除授权失败');
    }
  };

  const readyCount = Number(Boolean(status?.cookie.configured)) + Number(Boolean(status?.oauth.configured));
  const qrImage = qrMode === 'cookie' && qrUid
    ? extras.auth115CookieQrImageUrl(qrUid)
    : qrUid ? extras.auth115QrImageUrl(qrUid) : '';

  return (
    <Card className="overflow-hidden rounded-3xl border-slate-800 bg-[#15131c] text-white shadow-[0_24px_80px_rgba(15,8,25,.2)]">
      <div className="relative overflow-hidden border-b border-white/10 px-5 py-6 sm:px-7">
        <div className="pointer-events-none absolute -right-20 -top-24 size-72 rounded-full bg-fuchsia-500/15 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 left-1/4 size-56 rounded-full bg-violet-500/10 blur-3xl" />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-fuchsia-200">
              <ShieldCheck className="size-5" />
              <span className="text-xs font-semibold uppercase tracking-[.18em]">115 Account Center</span>
            </div>
            <h2 className="mt-3 text-2xl font-bold tracking-tight">115 混合鉴权</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">
              两条通道各司其职：Cookie 接收分享，OAuth 管理文件；扫码完成后自动保存并维护登录态。
            </p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3 text-right backdrop-blur">
            <p className="text-[11px] uppercase tracking-[.16em] text-slate-400">Ready channels</p>
            <p className="mt-1 text-xl font-bold">{readyCount}<span className="ml-1 text-sm font-normal text-slate-400">/ 2</span></p>
          </div>
        </div>
      </div>

      <CardContent className="space-y-5 p-5 sm:p-7">
        {statusError && (
          <div role="alert" className="rounded-2xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-200">
            {statusError}
            <Button size="sm" variant="ghost" className="ml-2 text-rose-100" onClick={() => void loadStatus()}>重新加载</Button>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-[1fr_auto_1fr] lg:items-stretch">
          <ChannelCard
            icon={<Link2 className="size-5" />}
            eyebrow="Share channel"
            title="网页 Cookie"
            state={status?.cookie.state ?? 'unknown'}
            configured={Boolean(status?.cookie.configured)}
            description="浏览分享内容、接收分享文件，是影视转存链路的必需通道。"
            detail={status?.cookie.configured ? `凭证来源：${status.cookie.source === 'file' ? '扫码或手动保存' : '环境变量'}` : '未配置时无法执行分享转存。'}
            danger={status?.cookie.error}
          />
          <div className="hidden items-center justify-center lg:flex">
            <div className="grid size-9 place-items-center rounded-full border border-white/10 bg-white/5 text-slate-400">+</div>
          </div>
          <ChannelCard
            icon={<FolderCog className="size-5" />}
            eyebrow="File channel"
            title="开放平台 OAuth"
            state={status?.oauth.state ?? 'unknown'}
            configured={Boolean(status?.oauth.configured)}
            description="负责建目录、改名、移动与删除；令牌到期前自动刷新并持久化。"
            detail={status?.oauth.configured ? `AppID ${status.oauth.app_id ?? ''} · 写操作优先走 OAuth` : '未授权时写操作临时回退 Cookie。'}
            danger={status?.oauth.error}
          >
            {status?.oauth.configured && (
              <Button size="sm" variant="ghost" className="mt-2 h-7 px-2 text-xs text-rose-300 hover:bg-rose-400/10 hover:text-rose-200" onClick={() => void clearOauth()}>
                <Trash2 className="size-3.5" />清除授权
              </Button>
            )}
          </ChannelCard>
        </div>

        {status?.traffic?.cooling_down && (
          <div className="rounded-2xl border border-amber-300/30 bg-amber-300/10 p-4 text-sm text-amber-100">
            115 接口正在风控冷却，约 {status.traffic.cooldown_remaining ?? 0} 秒后恢复。期间请求会自动等待，不会持续撞限流。
          </div>
        )}

        <section className="rounded-2xl border border-white/10 bg-[#211b29] p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[.18em] text-fuchsia-300">Quick actions</p>
              <h3 className="mt-1 text-base font-semibold">选择要建立的连接</h3>
            </div>
            <p className="text-xs text-slate-400">推荐先完成 Cookie，再授权 OAuth</p>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <Button className="h-auto min-h-20 flex-col gap-2 rounded-2xl border border-fuchsia-300/20 bg-fuchsia-400/10 py-4 text-fuchsia-50 hover:bg-fuchsia-400/20"
                    disabled={Boolean(qrMode) || qrBusy !== null} onClick={() => void startQr('cookie')}>
              {qrBusy === 'cookie' ? <Loader2 className="size-5 animate-spin" /> : <ScanLine className="size-5" />}
              <span>{qrMode === 'cookie' ? '等待扫码确认' : '扫码获取 Cookie'}</span>
            </Button>
            <Button className="h-auto min-h-20 flex-col gap-2 rounded-2xl border border-violet-300/20 bg-violet-400/10 py-4 text-violet-50 hover:bg-violet-400/20"
                    disabled={Boolean(qrMode) || qrBusy !== null} onClick={() => void startQr('oauth')}>
              {qrBusy === 'oauth' ? <Loader2 className="size-5 animate-spin" /> : <QrCode className="size-5" />}
              <span>{qrMode === 'oauth' ? '等待扫码确认' : '授权开放平台'}</span>
            </Button>
            <Button variant="outline" className="h-12 rounded-2xl border-white/10 bg-white/5 text-slate-100 hover:bg-white/10 hover:text-white"
                    disabled={pinging} onClick={() => void ping()}>
              {pinging ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}立即探活
            </Button>
            <Button variant="outline" className="h-12 rounded-2xl border-white/10 bg-white/5 text-slate-100 hover:bg-white/10 hover:text-white"
                    onClick={() => void loadStatus()}>
              <Cloud className="size-4" />刷新状态
            </Button>
          </div>

          {qrUrl && qrUid && qrMode && (
            <div className="mt-5 grid gap-4 rounded-2xl border border-white/10 bg-black/25 p-4 sm:grid-cols-[184px_1fr] sm:items-center">
              <img src={qrImage} alt={qrMode === 'cookie' ? '115 Cookie 登录二维码' : '115 OAuth 授权二维码'}
                   className="mx-auto size-44 rounded-xl bg-white p-2 shadow-xl" />
              <div>
                <p className="text-xs font-semibold uppercase tracking-[.18em] text-fuchsia-300">Scan with 115 App</p>
                <h4 className="mt-2 text-lg font-semibold">{qrMode === 'cookie' ? '获取分享转存 Cookie' : '授权文件管理能力'}</h4>
                <p className="mt-2 text-sm leading-6 text-slate-300">{qrMsg}</p>
                <p className="mt-3 text-xs leading-5 text-slate-500">请直接扫描二维码，不要点击二维码内容。Cookie 登录默认绑定独立 TV 会话，减少与现有网页端登录冲突。</p>
              </div>
            </div>
          )}
        </section>

        <details className="group rounded-2xl border border-white/10 bg-white/[0.035]">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-4 text-sm font-medium text-slate-200">
            <span className="flex items-center gap-2"><KeyRound className="size-4 text-slate-400" />高级选项：手动更新 Cookie</span>
            <ChevronDown className="size-4 text-slate-400 transition group-open:rotate-180" />
          </summary>
          <div className="border-t border-white/10 p-4">
            <p className="mb-2 text-xs leading-5 text-slate-400">仅在扫码不可用时使用。粘贴包含 UID、CID、SEID、KID 的完整 Cookie，保存后会自动探活。</p>
            <Textarea value={cookieText} onChange={(e) => setCookieText(e.target.value)}
                      placeholder="UID=...; CID=...; SEID=...; KID=..." spellCheck={false}
                      className="min-h-24 border-white/10 bg-black/25 font-mono text-xs text-slate-100 placeholder:text-slate-600" />
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
