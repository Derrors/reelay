/** 系统配置里的服务与运行参数：AI 与 TMDB、Telegram、115 请求限速、运行与安全。 */
import { useEffect, useRef, useState } from 'react';
import { Bot, CircleAlert, CircleCheck, Film, Gauge, Info, Loader2, PlugZap, RefreshCw, Send, ServerCog, ShieldCheck, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { ai, type ILlmTest } from '@/api/ai';
import { Panel } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { ISecretStatus } from '@/lib/types';
import { Field, Notice } from './fields';
import { PasswordField, SecretField } from './SecretField';
import { CHOICE_LABELS, obj, text, type Obj } from './shared';

type SetField = (key: string, name: string, value: unknown) => void;
type SecretProps = { secrets: ISecretStatus; onSecrets: (status: ISecretStatus) => void };

const numberOrBlank = (raw: string) => (raw === '' ? '' : Number(raw));
const RESTART_HINT = '修改后需要重启主进程（Docker：docker compose restart worker）才生效。';

function NumberField({ label, hint, unit, value, onChange, min, max, step }: {
  label: string; hint?: string; unit?: string; value: unknown; onChange: (value: unknown) => void;
  min?: number; max?: number; step?: number;
}) {
  return (
    <Field label={label} hint={hint}>
      <div className="relative">
        <Input type="number" min={min} max={max} step={step} className="bg-card pr-12 tabular-nums"
          value={text(value)} onChange={e => onChange(numberOrBlank(e.target.value))} />
        {unit ? <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">{unit}</span> : null}
      </div>
    </Field>
  );
}

const EFFORTS = [
  { value: '', label: '默认（不传，由模型决定）' }, { value: 'none', label: '关闭推理（none）' },
  { value: 'minimal', label: '最低（minimal）' }, { value: 'low', label: '低（low）' },
  { value: 'medium', label: '中（medium）' }, { value: 'high', label: '高（high）' },
];
const errorText = (e: unknown) => (e instanceof Error ? e.message : '请求失败');

/** 模型：只能从接口返回的列表里选。打开页面时用已保存的密钥自动获取一次；改了接口地址后点「刷新列表」。
 * 测试用表单里当前的地址、模型与推理强度（不必先保存）。 */
function ModelFields({ llm, setField, keySet }: { llm: Obj; setField: SetField; keySet: boolean }) {
  // url：这份列表是用哪个接口地址取的；地址改了就提示刷新
  const [list, setList] = useState<{ url: string; models: string[]; error?: string } | null>(null);
  const [loading, setLoading] = useState<'models' | 'test' | null>(null);
  const [result, setResult] = useState<ILlmTest | null>(null);
  const form = { base_url: text(llm.base_url).trim(), model: text(llm.model).trim(), reasoning_effort: text(llm.reasoning_effort) };
  const current = form.model;

  const load = (url: string) => ai.llmModels({ base_url: url })
    .then(r => setList({ url, models: r.models }))
    .catch((e: unknown) => setList({ url, models: [], error: errorText(e) }));

  const initialUrl = useRef(form.base_url);
  useEffect(() => {
    if (keySet) void load(initialUrl.current);
  }, [keySet]);

  const refresh = async () => {
    setLoading('models');
    await load(form.base_url);
    setLoading(null);
  };
  const test = async () => {
    setLoading('test'); setResult(null);
    try { setResult(await ai.llmTest(form)); } catch (e) { toast.error(errorText(e)); } finally { setLoading(null); }
  };

  const models = list?.models ?? [];
  const options = [
    // 已保存的模型不在列表里（或列表还没取到）时也要显示出来，但只能从列表里换成别的
    ...(current && !models.includes(current)
      ? [{ value: current, label: list?.models.length ? `${current}（接口列表里没有）` : current }] : []),
    ...models.map(m => ({ value: m, label: m })),
  ];
  const stale = list !== null && list.url !== form.base_url;
  const hint = !keySet ? '先保存 AI 接口密钥，再从接口获取可用的模型。'
    : list === null ? '正在获取模型列表…'
    : list.error ? `获取模型列表失败：${list.error}`
    : stale ? '接口地址改了，点「刷新列表」获取这个地址的模型。'
    : models.length ? `接口提供 ${models.length} 个模型，从中选择。` : '接口没有返回任何模型。';

  return (
    <>
      <Field label="模型" hint={<span className={list?.error ? 'text-destructive' : undefined}>{hint}</span>}>
        <div className="flex gap-2">
          <Select className="min-w-0 flex-1" value={current} options={options} disabled={!models.length}
            placeholder={current ? undefined : '请选择模型'}
            onValueChange={v => { setField('llm', 'model', v); setResult(null); }} />
          <Button variant="outline" disabled={!keySet || loading !== null} onClick={() => void refresh()}
            title={keySet ? undefined : '请先保存 AI 接口密钥'}>
            {loading === 'models' ? <Loader2 className="animate-spin" /> : <RefreshCw />}刷新列表
          </Button>
        </div>
      </Field>
      <Field label="推理强度" hint="对支持推理的模型生效：强度越高越慢、输出 token 越多。不支持的模型可能报错，改了先测试一下。">
        <Select value={text(llm.reasoning_effort)} options={EFFORTS}
          onValueChange={v => { setField('llm', 'reasoning_effort', v); setResult(null); }} />
      </Field>
      <div className="space-y-2">
        <Button variant="outline" size="sm" disabled={!keySet || !form.model || loading !== null} onClick={() => void test()}
          title={keySet ? undefined : '请先保存 AI 接口密钥'}>
          {loading === 'test' ? <Loader2 className="animate-spin" /> : <PlugZap />}测试连通性
        </Button>
        {result ? (
          result.ok ? (
            <p className="flex items-start gap-1.5 text-xs leading-5 text-success">
              <CircleCheck className="mt-0.5 size-3.5 shrink-0" />
              <span>可以使用：{result.model} 回复「{result.reply || '（空）'}」，用时 {result.seconds} 秒
                {typeof result.completion_tokens === 'number' ? `，输出 ${result.completion_tokens} token` : ''}。</span>
            </p>
          ) : (
            <p className="flex items-start gap-1.5 text-xs leading-5 text-destructive">
              <CircleAlert className="mt-0.5 size-3.5 shrink-0" /><span className="break-all">{result.error}</span>
            </p>
          )
        ) : <p className="text-xs leading-5 text-muted-foreground">用当前填写的地址、模型与推理强度发一条很短的消息，不必先保存。</p>}
      </div>
    </>
  );
}

export function ServicesTab({ config, setField, secrets, onSecrets }: { config: Obj; setField: SetField } & SecretProps) {
  const llm = obj(config.llm);
  const tmdb = obj(config.tmdb);
  return (
    <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
      <Panel icon={Sparkles} title="AI 服务" description="OpenAI 兼容接口，用于识别消息、转人工前的方案、审批建议与对话式控制 Bot。" bodyClassName="p-4 sm:p-5">
        <div className="space-y-4">
          <SecretField name="llm_api_key" secrets={secrets} onSaved={onSecrets} placeholder="sk-…"
            hint="未设置时识别改用规则，并且每条都转给你确认。" />
          <Field label="接口地址" hint="留空使用 OpenAI 官方地址；第三方或自建网关填到 /v1 为止。">
            <Input className="bg-card" placeholder="https://api.openai.com/v1" value={text(llm.base_url)}
              onChange={e => setField('llm', 'base_url', e.target.value)} />
          </Field>
          <ModelFields llm={llm} setField={setField} keySet={Boolean(secrets.llm_api_key?.set)} />
        </div>
      </Panel>

      <Panel icon={Film} title="TMDB" description="识别后补全规范片名、类型与海报；追剧订阅找剧与缺集追更也会用到。" bodyClassName="p-4 sm:p-5">
        <div className="space-y-4">
          <SecretField name="tmdb_api_key" secrets={secrets} onSaved={onSecrets}
            hint="v3 API Key 或 v4 Read Access Token（eyJ 开头）均可；未设置时沿用 AI 识别的片名。" />
          <Field label="接口地址" hint="留空使用官方地址；国内网络连不上时可改用 https://api.tmdb.org/3 或自己的反向代理。">
            <Input className="bg-card" placeholder="https://api.themoviedb.org/3" value={text(tmdb.base_url)}
              onChange={e => setField('tmdb', 'base_url', e.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="匹配语言">
              <Select value={text(tmdb.language)} onValueChange={v => setField('tmdb', 'language', v)}
                options={['zh-CN', 'zh-TW', 'en-US'].map(value => ({ value, label: CHOICE_LABELS[value] ?? value }))} />
            </Field>
            <NumberField label="请求超时" unit="秒" min={1} max={60} value={tmdb.timeout}
              onChange={v => setField('tmdb', 'timeout', v)} />
          </div>
        </div>
      </Panel>
    </div>
  );
}

export function TelegramTab({ config, setField, secrets, onSecrets }: { config: Obj; setField: SetField } & SecretProps) {
  const telegram = obj(config.telegram);
  return (
    <div className="space-y-5">
      <Notice tone="info" icon={Info}>
        Telegram 账号、控制 Bot 与机主 ID {RESTART_HINT}首次使用个人账号监听时，还要在终端运行一次
        <code className="mx-1 rounded bg-muted px-1 font-mono text-xs">python -m reelay.login</code>登录。
      </Notice>
      <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
        <Panel icon={Send} title="个人账号（监听来源）" description="在 my.telegram.org 申请 API ID 与 API Hash，用你自己的账号收听资源 Bot 与频道。" bodyClassName="p-4 sm:p-5">
          <div className="space-y-4">
            <SecretField name="telegram_api_id" secrets={secrets} onSaved={onSecrets} placeholder="123456" />
            <SecretField name="telegram_api_hash" secrets={secrets} onSaved={onSecrets} />
            <div className="grid gap-4 sm:grid-cols-2">
              <NumberField label="重连后最多补拉" unit="条" min={0} max={5000} value={telegram.backfill_limit}
                hint="每个来源；0 表示不补拉停机期间的消息。" onChange={v => setField('telegram', 'backfill_limit', v)} />
              <NumberField label="只补拉最近" unit="天" min={0} max={365} value={telegram.backfill_max_days}
                onChange={v => setField('telegram', 'backfill_max_days', v)} />
              <NumberField label="断开多久告警" unit="秒" min={10} max={86400} value={telegram.alert_after_sec}
                onChange={v => setField('telegram', 'alert_after_sec', v)} />
            </div>
          </div>
        </Panel>
        <Panel icon={Bot} title="控制 Bot" description="推送确认卡片与通知，并用一句话查询与操作；只服务机主本人。" bodyClassName="p-4 sm:p-5">
          <div className="space-y-4">
            <SecretField name="control_bot_token" secrets={secrets} onSaved={onSecrets} placeholder="123456:ABC…"
              hint="向 @BotFather 创建 Bot 后获得。" />
            <Field label="机主 Telegram ID" hint="你自己的数字 ID，给 @userinfobot 发消息可得；留空不启用控制 Bot。">
              <Input className="bg-card tabular-nums" inputMode="numeric" value={text(telegram.owner_id)}
                onChange={e => setField('telegram', 'owner_id', e.target.value.trim())} />
            </Field>
          </div>
        </Panel>
      </div>
    </div>
  );
}

/** 115 请求限速：放在「115 网盘」页的扫码登录下面。 */
export function P115LimitsPanel({ config, setField }: { config: Obj; setField: SetField }) {
  const p115 = obj(config.p115);
  return (
    <Panel icon={Gauge} title="请求限速" description="各类接口每秒最多发几次请求；触发 115 风控（访问上限、操作频繁）后所有接口暂停一段时间。保存后立即生效。" bodyClassName="p-4 sm:p-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <NumberField label="开放平台接口" unit="次/秒" min={0.1} max={20} step={0.5} value={p115.open_qps}
          onChange={v => setField('p115', 'open_qps', v)} />
        <NumberField label="网页接口" unit="次/秒" min={0.1} max={20} step={0.5} value={p115.cookie_qps}
          onChange={v => setField('p115', 'cookie_qps', v)} />
        <NumberField label="分享与转存" unit="次/秒" min={0.1} max={20} step={0.5} value={p115.share_qps}
          onChange={v => setField('p115', 'share_qps', v)} />
        <NumberField label="风控后暂停" unit="秒" min={60} max={86400} value={p115.risk_cooldown_sec}
          onChange={v => setField('p115', 'risk_cooldown_sec', v)} />
      </div>
    </Panel>
  );
}

const LOG_LEVELS = [
  { value: 'DEBUG', label: '调试（DEBUG）' }, { value: 'INFO', label: '一般（INFO）' },
  { value: 'WARNING', label: '警告（WARNING）' }, { value: 'ERROR', label: '错误（ERROR）' },
];

export function SystemTab({ config, setField, secrets, onSecrets }: { config: Obj; setField: SetField } & SecretProps) {
  const runtime = obj(config.runtime);
  return (
    <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
      <Panel icon={ShieldCheck} title="访问安全" bodyClassName="p-4 sm:p-5">
        <PasswordField secrets={secrets} onSaved={onSecrets} />
      </Panel>
      <Panel icon={ServerCog} title="运行参数" description={RESTART_HINT} bodyClassName="p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField label="同时转存的任务数" min={1} max={20} value={runtime.concurrency}
            hint="调大会加快积压处理，但更容易触发 115 风控。" onChange={v => setField('runtime', 'concurrency', v)} />
          <Field label="日志级别" hint="排查问题时改为调试，平时用一般即可。">
            <Select value={text(runtime.log_level)} onValueChange={v => setField('runtime', 'log_level', v)} options={LOG_LEVELS} />
          </Field>
        </div>
        <p className="mt-4 text-xs leading-5 text-muted-foreground">
          数据库、配置、密钥、会话、115 凭证与日志都固定放在 data/ 目录（Docker 为数据卷 /app/data），备份时整体复制即可。
        </p>
      </Panel>
    </div>
  );
}
