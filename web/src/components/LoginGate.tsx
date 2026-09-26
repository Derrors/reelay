import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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
      setErr('密码错误');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-5">
      <Card className="w-full max-w-sm rounded-md shadow-none">
        <CardHeader>
          <CardTitle className="text-lg">Reelay 控制台登录</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-3">
            <Input
              type="password"
              placeholder="访问密码"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              autoFocus
            />
            {err ? <p className="text-xs text-destructive">{err}</p> : null}
            <Button type="submit" className="w-full" disabled={busy || !pw}>
              {busy ? '验证中…' : '进入'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
