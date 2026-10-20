import { CircleAlert, Clapperboard, Eye, EyeOff, LoaderCircle, Lock } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { apiUrl } from '@/api/client';
import { ThemeToggle } from '@/components/app/ThemeToggle';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

class LoginError extends Error {
  /** 被限流时，多少秒后可以再试。 */
  retryAfter: number;

  constructor(message: string, retryAfter = 0) {
    super(message);
    this.retryAfter = retryAfter;
  }
}

/**
 * 直接请求登录接口：extras.req 会把所有 401 统一改写成「登录已失效」，
 * 这里需要区分密码错误（401）和尝试过于频繁（429）。
 */
async function login(password: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(apiUrl('auth/login'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
  } catch {
    throw new LoginError('无法连接到服务，请检查网络后重试');
  }
  if (response.ok) return;
  let detail = '';
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') detail = body.detail;
  } catch {
    // 非 JSON 响应，使用下面的通用文案
  }
  if (response.status === 401) throw new LoginError('访问密码不正确，请重试');
  if (response.status === 429) {
    const seconds = Number(/(\d+)\s*秒/.exec(detail)?.[1] ?? 60);
    throw new LoginError(detail || '尝试过于频繁，请稍后再试', seconds);
  }
  throw new LoginError(detail || `登录失败（${response.status}），请稍后重试`);
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
      await login(password);
      onOk();
    } catch (e) {
      const err = e instanceof LoginError ? e : new LoginError('登录失败，请稍后重试');
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
          <p className="mt-1 text-sm text-muted-foreground">输入访问密码进入控制台</p>
        </div>

        <form
          onSubmit={(e) => void submit(e)}
          className="mt-7 rounded-xl border border-card-border bg-card p-5 shadow-lg sm:p-6"
        >
          <label htmlFor="login-password" className="text-[13px] font-medium">访问密码</label>
          <div className="relative mt-1.5">
            <Lock className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={input}
              id="login-password"
              type={visible ? 'text' : 'password'}
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={Boolean(error) || undefined}
              aria-describedby={error ? 'login-error' : undefined}
              className="h-10 bg-card pl-9 pr-10"
            />
            <button
              type="button"
              onClick={() => setVisible((v) => !v)}
              aria-label={visible ? '隐藏密码' : '显示密码'}
              className="absolute right-1 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          </div>
          {error ? (
            <p id="login-error" role="alert" className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-destructive">
              <CircleAlert className="mt-0.5 size-3.5 shrink-0" />
              <span>{remaining ? `尝试过于频繁，请 ${remaining} 秒后再试` : error}</span>
            </p>
          ) : null}
          <Button type="submit" className="mt-4 h-10 w-full" disabled={busy || !password || remaining > 0}>
            {busy ? <LoaderCircle className="animate-spin" /> : null}
            {busy ? '正在验证…' : remaining ? `${remaining} 秒后可重试` : '进入控制台'}
          </Button>
        </form>

        <p className="mt-5 text-center text-xs leading-5 text-muted-foreground">
          密码在「设置 → 运行与安全」中设置；忘记时删掉 <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">data/secrets.json</code> 里的 web_password
        </p>
      </main>
      <ThemeToggle className="absolute bottom-5 right-5" />
    </div>
  );
}
