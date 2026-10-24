import { CircleAlert, Clapperboard, Eye, EyeOff, LoaderCircle, Lock, ShieldAlert } from 'lucide-react';
import { type ReactNode, type Ref, useEffect, useRef, useState } from 'react';
import { authApi } from '@/api/auth';
import { ApiError } from '@/api/request';
import { ThemeToggle } from '@/components/app/ThemeToggle';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

/** 与后端 MIN_PASSWORD_LENGTH 一致。 */
const MIN_PASSWORD_LENGTH = 8;

/** 登录失败的提示，以及被限流时多少秒后可以再试。 */
function loginError(e: unknown): { message: string; retryAfter: number } {
  if (!(e instanceof ApiError)) return { message: '登录失败，请稍后重试', retryAfter: 0 };
  if (e.status === 401) return { message: '访问密码不正确，请重试', retryAfter: 0 };
  if (e.status === 429) return { message: e.message, retryAfter: Number(/(\d+)\s*秒/.exec(e.message)?.[1] ?? 60) };
  return { message: e.message, retryAfter: 0 };
}

/** 登录页、设置密码页与「无法访问」页共用的外框：品牌标识、一句说明与下方内容。 */
function GateShell({ subtitle, children }: { subtitle: string; children: ReactNode }) {
  return (
    <div className="relative flex min-h-svh flex-col items-center justify-center px-5 py-10">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px]"
        style={{ background: 'radial-gradient(640px 320px at 50% -60px, color-mix(in oklab, var(--primary) 16%, transparent), transparent 70%)' }}
      />
      <main className="relative w-full max-w-[360px]">
        <div className="flex flex-col items-center text-center">
          <div className="grid size-12 place-items-center rounded-[14px] bg-gradient-to-br from-[hsl(171_58%_44%)] to-[hsl(178_64%_26%)] text-white shadow-[0_8px_20px_-6px_hsl(173_60%_30%/0.6)]">
            <Clapperboard className="size-6" />
          </div>
          <h1 className="mt-4 text-[22px] font-semibold tracking-tight">Reelay</h1>
          <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
        </div>
        {children}
      </main>
      <ThemeToggle className="absolute bottom-5 right-5" />
    </div>
  );
}

const CARD = 'mt-7 rounded-xl border border-card-border bg-card p-5 shadow-lg sm:p-6';

function GateNotice({ message }: { message: string }) {
  return (
    <div role="alert" className={`${CARD} flex items-start gap-2.5 text-sm leading-6`}>
      <ShieldAlert className="mt-1 size-4 shrink-0 text-warning" />
      <p>{message}</p>
    </div>
  );
}

interface PasswordInputProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  visible: boolean;
  onToggle: () => void;
  autoComplete: string;
  autoFocus?: boolean;
  invalid?: boolean;
  describedBy?: string;
  inputRef?: Ref<HTMLInputElement>;
}

/** 带锁图标与「显示 / 隐藏」按钮的密码框。 */
function PasswordInput({ id, value, onChange, visible, onToggle, autoComplete, autoFocus, invalid, describedBy, inputRef }: PasswordInputProps) {
  return (
    <div className="relative mt-1.5">
      <Lock className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        ref={inputRef}
        id={id}
        type={visible ? 'text' : 'password'}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        className="h-10 bg-card pl-9 pr-10"
      />
      <button
        type="button"
        onClick={onToggle}
        aria-label={visible ? '隐藏密码' : '显示密码'}
        className="absolute right-0.5 top-1/2 grid size-9 -translate-y-1/2 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

function FormError({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} role="alert" className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-destructive">
      <CircleAlert className="mt-0.5 size-3.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

const SECRETS_FILE = <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">data/secrets.json</code>;

/** 服务端拒绝这次访问：说明原因，不进入控制台。 */
export function AccessDenied({ message }: { message: string }) {
  return (
    <GateShell subtitle="无法从这里打开控制台">
      <GateNotice message={message} />
    </GateShell>
  );
}

interface SetupGateProps {
  /** 这个地址不能做首次设置的原因（如用域名打开、不在局域网内）；为空时显示设置表单。 */
  blocked: string | null;
  onDone: () => void;
  /** 期间已经在别的页面设好了密码：改为显示登录页。 */
  onExisting: () => void;
}

/** 首次打开管理台：先设置登录密码，设好后直接进入控制台。 */
export function SetupGate({ blocked, onDone, onExisting }: SetupGateProps) {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  if (blocked) {
    return (
      <GateShell subtitle="首次使用，先设置登录密码">
        <GateNotice message={blocked} />
      </GateShell>
    );
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (password.trim().length < MIN_PASSWORD_LENGTH) return setError(`密码至少 ${MIN_PASSWORD_LENGTH} 个字符`);
    if (password !== confirm) return setError('两次输入的密码不一致');
    setBusy(true);
    setError('');
    try {
      await authApi.setup(password);
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) return onExisting();
      setError(e instanceof Error ? e.message : '设置失败，请稍后重试');
    } finally {
      setBusy(false);
    }
  };

  return (
    <GateShell subtitle="首次使用，先设置登录密码">
      <form onSubmit={(e) => void submit(e)} className={CARD}>
        <label htmlFor="setup-password" className="text-[13px] font-medium">登录密码</label>
        <PasswordInput
          id="setup-password" value={password} onChange={setPassword} visible={visible}
          onToggle={() => setVisible((v) => !v)} autoComplete="new-password" autoFocus describedBy="setup-hint"
        />
        <p id="setup-hint" className="mt-1.5 text-xs text-muted-foreground">至少 {MIN_PASSWORD_LENGTH} 个字符</p>
        <label htmlFor="setup-confirm" className="mt-4 block text-[13px] font-medium">再输入一次</label>
        <PasswordInput
          id="setup-confirm" value={confirm} onChange={setConfirm} visible={visible}
          onToggle={() => setVisible((v) => !v)} autoComplete="new-password"
          invalid={Boolean(error)} describedBy={error ? 'setup-error' : undefined}
        />
        {error ? <FormError id="setup-error">{error}</FormError> : null}
        <Button type="submit" className="mt-4 h-10 w-full" disabled={busy || !password || !confirm}>
          {busy ? <LoaderCircle className="animate-spin" /> : null}
          {busy ? '正在保存…' : '设置密码并进入'}
        </Button>
      </form>

      <p className="mt-5 text-center text-xs leading-5 text-muted-foreground">
        之后从任何设备打开控制台都要输入这个密码，可以在「设置 → 运行与安全」修改；忘记时删掉 {SECRETS_FILE} 里的 web_password，再打开管理台重新设置
      </p>
    </GateShell>
  );
}

export default function LoginGate({ onOk }: { onOk: () => void }) {
  const [password, setPassword] = useState('');
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [lockedUntil, setLockedUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const input = useRef<HTMLInputElement>(null);

  const remaining = Math.max(0, Math.ceil((lockedUntil - now) / 1000));

  // 被限流期间每秒刷新倒计时，结束后清掉提示。
  useEffect(() => {
    if (!lockedUntil) return;
    const timer = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t >= lockedUntil) {
        setLockedUntil(0);
        setError('');
        window.clearInterval(timer);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [lockedUntil]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!password || busy || remaining) return;
    setBusy(true);
    setError('');
    try {
      await authApi.login(password);
      onOk();
    } catch (e) {
      const err = loginError(e);
      setError(err.message);
      if (err.retryAfter) {
        setNow(Date.now());
        setLockedUntil(Date.now() + err.retryAfter * 1000);
      }
      requestAnimationFrame(() => input.current?.select());
    } finally {
      setBusy(false);
    }
  };

  return (
    <GateShell subtitle="输入访问密码进入控制台">
      <form onSubmit={(e) => void submit(e)} className={CARD}>
        <label htmlFor="login-password" className="text-[13px] font-medium">访问密码</label>
        <PasswordInput
          id="login-password" value={password} onChange={setPassword} visible={visible}
          onToggle={() => setVisible((v) => !v)} autoComplete="current-password" autoFocus inputRef={input}
          invalid={Boolean(error)} describedBy={error ? 'login-error' : undefined}
        />
        {error ? (
          <FormError id="login-error">{remaining ? `尝试过于频繁，请 ${remaining} 秒后再试` : error}</FormError>
        ) : null}
        <Button type="submit" className="mt-4 h-10 w-full" disabled={busy || !password || remaining > 0}>
          {busy ? <LoaderCircle className="animate-spin" /> : null}
          {busy ? '正在验证…' : remaining ? `${remaining} 秒后可重试` : '进入控制台'}
        </Button>
      </form>

      <p className="mt-5 text-center text-xs leading-5 text-muted-foreground">
        密码可以在「设置 → 运行与安全」修改；忘记时删掉 {SECRETS_FILE} 里的 web_password，再打开管理台重新设置
      </p>
    </GateShell>
  );
}
