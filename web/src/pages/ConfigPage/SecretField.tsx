import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { configApi } from '@/api/config';
import { ToneBadge } from '@/components/app/StatusBadge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { confirmDialog } from '@/lib/confirm';
import type { ISecretStatus } from '@/lib/types';
import { errorMessage } from '@/lib/utils';

/** 32 字节随机数的 base64url，用作访问令牌。 */
function randomToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * 密钥：只显示是否已设置，可以替换或清除，读不回原值。
 * 单独保存、立即生效，不经过页面底部的「保存并生效」。
 * generate：可随机生成一个令牌，生成后明文显示，便于保存前复制。
 */
export function SecretField({ name, secrets, onSaved, hint, placeholder, generate }: {
  name: string; secrets: ISecretStatus; onSaved: (status: ISecretStatus) => void;
  hint?: string; placeholder?: string; generate?: boolean;
}) {
  const item = secrets[name];
  const label = item?.label ?? name;
  const isSet = Boolean(item?.set);
  const [value, setValue] = useState('');
  const [revealed, setRevealed] = useState(false);
  const [busy, setBusy] = useState(false);

  const save = async (next: string | null) => {
    if (next === null && !(await confirmDialog({
      title: `清除「${label}」？`, description: '清除后相关功能会停用。', confirmText: '清除', destructive: true,
    }))) return;
    setBusy(true);
    try {
      onSaved(await configApi.saveSecrets({ [name]: next }));
      setValue('');
      setRevealed(false);
      toast.success(next === null ? `已清除「${label}」` : `「${label}」已保存并生效`);
    } catch (e) {
      toast.error(errorMessage(e, '保存失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-medium leading-5">{label}</span>
        <ToneBadge tone={isSet ? 'success' : 'neutral'}>{isSet ? '已设置' : '未设置'}</ToneBadge>
      </div>
      <div className="flex gap-2">
        <Input
          type={revealed ? 'text' : 'password'} autoComplete="new-password" className="bg-card" aria-label={label}
          value={value} placeholder={isSet ? '输入新值以替换' : placeholder}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && value.trim()) void save(value.trim()); }}
        />
        {generate && !value ? (
          <Button variant="outline" disabled={busy} onClick={() => { setValue(randomToken()); setRevealed(true); }}>生成</Button>
        ) : null}
        <Button variant="outline" disabled={busy || !value.trim()} onClick={() => void save(value.trim())}>
          {busy ? <Loader2 className="animate-spin" /> : null}保存
        </Button>
        {isSet ? <Button variant="ghost" disabled={busy} onClick={() => void save(null)}>清除</Button> : null}
      </div>
      {hint ? <p className="text-xs leading-5 text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** Web 登录密码：已设置时修改或清除要先输入当前密码；修改后其他已登录的页面需要重新登录。 */
export function PasswordField({ secrets, onSaved }: { secrets: ISecretStatus; onSaved: (status: ISecretStatus) => void }) {
  const isSet = Boolean(secrets.web_password?.set);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const mismatch = Boolean(confirm) && next !== confirm;

  const save = async (value: string | null) => {
    if (value === null && !(await confirmDialog({
      title: '清除管理台密码？',
      description: '清除后访问管理台不再需要登录，只在本机使用时才建议清除。',
      confirmText: '清除密码', destructive: true,
    }))) return;
    setBusy(true);
    try {
      onSaved(await configApi.saveSecrets({ web_password: value }, current));
      setCurrent(''); setNext(''); setConfirm('');
      toast.success(value === null ? '已清除 Web 登录密码' : '密码已更新，其他已登录的页面需要重新登录');
    } catch (e) {
      toast.error(errorMessage(e, '保存失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-medium leading-5">Web 登录密码</span>
        <ToneBadge tone={isSet ? 'success' : 'warning'}>{isSet ? '已设置' : '未设置，访问无需登录'}</ToneBadge>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {isSet ? (
          <Input type="password" autoComplete="current-password" className="bg-card" aria-label="当前密码"
            placeholder="当前密码" value={current} onChange={e => setCurrent(e.target.value)} />
        ) : null}
        <Input type="password" autoComplete="new-password" className="bg-card" aria-label="新密码"
          placeholder="新密码" value={next} onChange={e => setNext(e.target.value)} />
        <Input type="password" autoComplete="new-password" className="bg-card" aria-label="再次输入新密码"
          placeholder="再次输入新密码" value={confirm} onChange={e => setConfirm(e.target.value)}
          aria-invalid={mismatch || undefined} />
      </div>
      {mismatch ? <p className="text-xs text-destructive">两次输入的新密码不一致</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" disabled={busy || !next || next !== confirm || (isSet && !current)}
          onClick={() => void save(next)}>
          {busy ? <Loader2 className="animate-spin" /> : null}{isSet ? '修改密码' : '设置密码'}
        </Button>
        {isSet ? (
          <Button variant="ghost" size="sm" disabled={busy || !current} onClick={() => void save(null)}>清除密码</Button>
        ) : null}
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        管理台暴露到本机以外（局域网、公网、反向代理）时务必设置。忘记密码时删掉 data/secrets.json 里的 web_password 一项即可恢复免登录。
      </p>
    </div>
  );
}
