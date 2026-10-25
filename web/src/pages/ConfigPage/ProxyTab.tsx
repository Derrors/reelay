/** 网络代理：Telegram、TMDB 与 AI 服务经 HTTP 或 SOCKS5 代理连接；115 在国内，始终直连。 */
import { useState } from 'react';
import { CircleAlert, CircleCheck, Globe, Info, Loader2, PlugZap, Route } from 'lucide-react';
import { toast } from 'sonner';
import { configApi, type IProxyTestResult } from '@/api/config';
import { Field } from '@/components/app/Field';
import { Notice } from '@/components/app/Notice';
import { Panel } from '@/components/app/Panel';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { ISecretStatus } from '@/lib/types';
import { errorMessage } from '@/lib/utils';
import { obj, text, type Obj } from '@/lib/values';
import { SwitchRow } from './fields';
import { SecretField } from './SecretField';
import { normalizeUrl } from './shared';

const SERVICES = [
  { key: 'telegram', title: 'Telegram', description: '监听来源、控制 Bot 与终端里的登录（python -m reelay.login）。' },
  { key: 'tmdb', title: 'TMDB', description: '补全片名与海报，追剧订阅找剧与缺集追更。' },
  { key: 'ai', title: 'AI 服务', description: '用国内的 AI 服务或本地模型时可以关掉。' },
];

/** 地址和已保存的不同时，已保存的代理密码属于原来的地址（保存设置时清除），新地址的密码先放在
 * pendingSecrets，保存设置后写入，也用于测试。 */
export function ProxyTab({ config, saved, setField, secrets, onSecrets, pendingSecrets, setPendingSecret }: {
  config: Obj; saved: Obj; setField: (key: string, name: string, value: unknown) => void;
  secrets: ISecretStatus; onSecrets: (status: ISecretStatus) => void;
  pendingSecrets: Record<string, string>; setPendingSecret: (name: string, value: string) => void;
}) {
  const proxy = obj(config.proxy);
  const url = normalizeUrl(proxy.url);
  const moved = url !== normalizeUrl(obj(saved.proxy).url);
  const passwordSet = Boolean(secrets.proxy_password?.set);
  const newPassword = pendingSecrets.proxy_password ?? '';
  const services = SERVICES.filter(s => proxy[s.key] === true).map(s => s.key);
  const [testing, setTesting] = useState(false);
  // key：这次结果用的地址与服务；改了就提示重新测试
  const [tested, setTested] = useState<{ key: string; results: IProxyTestResult[] } | null>(null);
  const testKey = `${url}|${services.join(',')}`;

  const test = async () => {
    setTesting(true); setTested(null);
    try {
      const r = await configApi.testProxy({ url, services, password: moved ? newPassword : undefined });
      setTested({ key: testKey, results: r.results });
    } catch (e) {
      toast.error(errorMessage(e, '测试失败'));
    } finally {
      setTesting(false);
    }
  };
  const disabledReason = !url ? '先填写代理地址' : !services.length ? '至少开启一项走代理的服务' : undefined;

  return (
    <div className="space-y-5">
      <Notice tone="info" icon={Info}>
        TMDB 与 AI 服务保存后立即生效；Telegram 要重启主进程（Docker：docker compose restart worker），还没连上 Telegram 时下次重试就会用上。115 的服务器在国内，始终直连。
      </Notice>
      <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
        <Panel icon={Globe} title="代理服务器" description="支持 HTTP 与 SOCKS5；Clash、mihomo、v2rayN 等客户端的 HTTP 端口或混合端口都可以用。"
          bodyClassName="p-4 sm:p-5">
          <div className="space-y-4">
            <Field label="代理地址"
              hint="留空不使用代理。需要认证时写成 http://用户名@地址:端口，密码填在下面。Docker 部署时 127.0.0.1 指容器自己，代理在宿主机或局域网里的其他设备上时填它的局域网地址。">
              <Input className="bg-card" placeholder="http://192.168.1.2:7890" value={text(proxy.url)}
                onChange={e => setField('proxy', 'url', e.target.value)} />
            </Field>
            {moved && url ? (
              <Field label="这个地址的密码"
                hint={passwordSet
                  ? '已保存的密码属于原来的地址，保存设置时会被清除；需要认证就在这里填新地址的密码，保存设置后一并写入（选填）。'
                  : '代理需要认证时填写，保存设置后一并写入（选填）；也用于下面的测试。'}>
                <Input type="password" autoComplete="new-password" className="bg-card"
                  value={newPassword} onChange={e => setPendingSecret('proxy_password', e.target.value)} />
              </Field>
            ) : !moved ? (
              <SecretField name="proxy_password" secrets={secrets} onSaved={onSecrets}
                hint="代理需要认证时填写，地址里要写上用户名；不需要认证就留空。" />
            ) : null}
          </div>
        </Panel>

        <Panel icon={Route} title="走代理的服务" description="没开代理的服务按原来的方式连接。" bodyClassName="p-4 sm:p-5">
          <div className="space-y-4">
            <div className="grid gap-2">
              {SERVICES.map(s => (
                <SwitchRow key={s.key} title={s.title} description={s.description} checked={proxy[s.key] === true}
                  onCheckedChange={v => setField('proxy', s.key, v)} />
              ))}
            </div>
            <div className="space-y-2">
              <Button variant="outline" size="sm" disabled={Boolean(disabledReason) || testing} title={disabledReason}
                onClick={() => void test()}>
                {testing ? <Loader2 className="animate-spin" /> : <PlugZap />}测试代理
              </Button>
              {tested ? (
                <ul className="space-y-1">
                  {tested.results.map(r => (
                    <li key={r.service} className={`flex items-start gap-1.5 text-xs leading-5 ${r.ok ? 'text-success' : 'text-destructive'}`}>
                      {r.ok ? <CircleCheck className="mt-0.5 size-3.5 shrink-0" /> : <CircleAlert className="mt-0.5 size-3.5 shrink-0" />}
                      <span className="break-all">{r.label}：{r.ok ? `可以连接，用时 ${r.seconds} 秒` : r.error}</span>
                    </li>
                  ))}
                  {tested.key !== testKey ? <li className="text-xs leading-5 text-muted-foreground">地址或服务改了，请重新测试。</li> : null}
                </ul>
              ) : (
                <p className="text-xs leading-5 text-muted-foreground">
                  用当前填写的地址经代理连一次开启的服务，不必先保存；Telegram 只建立连接，不登录。
                </p>
              )}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
