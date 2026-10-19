import { useState } from 'react';
import { Clapperboard, KeyRound, Lock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { extras } from '@/api/extras';

export default function LoginGate({ onOk }: { onOk: () => void }) {
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr('');
    try {
      await extras.login(pw);
      onOk();
    } catch {
      setErr('访问密码不正确，请重试');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden p-5">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(900px 420px at 80% -10%, rgba(22,140,126,.16), transparent 60%), radial-gradient(700px 380px at 0% 110%, rgba(58,120,200,.12), transparent 55%)',
        }}
      />
      <Card className="relative w-full max-w-sm rounded-2xl border-border/70 shadow-lg">
        <CardContent className="p-8">
          <div className="flex flex-col items-center text-center">
            <div className="grid size-12 place-items-center rounded-2xl bg-gradient-to-br from-[#2bb5a3] to-[#137a6e] text-white shadow-[0_8px_20px_-6px_rgba(20,140,126,.6)]">
              <Clapperboard className="size-6" />
            </div>
            <h1 className="mt-4 text-lg font-semibold tracking-tight">Reelay 转存控制台</h1>
            <p className="mt-1 text-[13px] text-muted-foreground">请输入访问密码进入</p>
          </div>
          <form onSubmit={submit} className="mt-6 space-y-3">
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="password"
                placeholder="访问密码"
                value={pw}
                onChange={(e) => setPw(e.target.value)}
                className="h-11 rounded-lg pl-9"
                autoFocus
              />
            </div>
            {err ? <p className="flex items-center gap-1.5 text-xs text-destructive"><Lock className="size-3.5" />{err}</p> : null}
            <Button type="submit" className="h-11 w-full rounded-lg" disabled={busy || !pw}>
              {busy ? '正在验证…' : '进入控制台'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
