import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { CircleAlert, Folder, FolderPlus, FolderSearch, Plus, Send, ShieldCheck, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import DirectoryPicker from '@/components/DirectoryPicker';
import { Panel } from '@/components/app/Panel';
import { Select } from '@/components/app/Select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { approvalLabels } from '@/lib/status';
import { cn } from '@/lib/utils';
import { Field, RadioCards, SwitchRow } from './fields';
import { RuleComposer } from './RuleComposer';
import { APPROVAL_HINTS, APPROVAL_OPTIONS, obj, providerLabel, rows, text, type Catalog, type Obj } from './shared';

const FORWARD_TOKENS: Array<[string, string]> = [
  ['{title}', '片名'], ['{year}', '年份'], ['{resolution}', '清晰度'], ['{size}', '大小'], ['{link}', '链接'],
];

interface BasicTabProps {
  config: Obj;
  catalog: Catalog | null;
  update: (key: string, value: unknown) => void;
  setField: (key: string, name: string, value: unknown) => void;
  /** 目录编号 → 名称；null 表示读取失败，缺省表示读取中。 */
  dirNames: Record<string, string | null>;
  rememberDir: (id: string, name: string) => void;
  /** 页面上是否有未保存的修改。 */
  dirty: boolean;
  /** 直接保存 AI 起草、用户确认过的配置块；成功返回 true。 */
  applySections: (sections: Record<string, unknown>, revision: string) => Promise<boolean>;
}

function Tag({ children, muted }: { children: React.ReactNode; muted?: boolean }) {
  return (
    <span className={cn(
      'rounded-full px-1.5 text-[11px] font-medium leading-[18px]',
      muted ? 'bg-muted text-muted-foreground' : 'bg-primary/10 text-primary',
    )}>{children}</span>
  );
}

export function BasicTab({ config, catalog, update, setField, dirNames, rememberDir, dirty, applySections }: BasicTabProps) {
  const dirs = obj(config.save_dirs);
  const defaults = obj(config.defaults);
  const forward = obj(config.forward);
  const sources = rows(config.sources);
  const aliases = Object.keys(dirs);
  const [newAlias, setNewAlias] = useState('');
  const [newDir, setNewDir] = useState<{ id: string; name: string } | null>(null);
  const [addError, setAddError] = useState('');
  const [picker, setPicker] = useState<{ alias?: string; cid: string; name: string } | null>(null);
  const templateRef = useRef<HTMLTextAreaElement>(null);

  const dirLabel = (cid: string) => (cid === '0' ? '根目录' : dirNames[cid]);
  const usage = (alias: string) => ({
    isDefault: text(defaults.save_to) === alias,
    sources: sources.filter(s => s.save_to === alias).length,
  });

  const addDir = () => {
    const alias = newAlias.trim();
    if (!alias) { setAddError('请给目录取个名字'); return; }
    if (Object.hasOwn(dirs, alias)) { setAddError(`已经有名为「${alias}」的目录`); return; }
    if (!newDir) { setAddError('请从 115 网盘中选择对应的目录'); return; }
    update('save_dirs', { ...dirs, [alias]: newDir.id });
    setNewAlias(''); setNewDir(null); setAddError('');
  };
  const removeDir = (alias: string) => {
    const u = usage(alias);
    const users = [u.isDefault && '默认规则', u.sources && `${u.sources} 个监听来源`].filter(Boolean);
    if (users.length) { toast.error(`「${alias}」正被${users.join('、')}使用，请先改用其他目录再删除`); return; }
    const next = { ...dirs };
    delete next[alias];
    update('save_dirs', next);
  };
  const insertToken = (token: string) => {
    const el = templateRef.current;
    const current = text(forward.template);
    const start = el?.selectionStart ?? current.length;
    const end = el?.selectionEnd ?? current.length;
    setField('forward', 'template', current.slice(0, start) + token + current.slice(end));
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(start + token.length, start + token.length); });
  };
  const preferencesActive = text(defaults.decision) === 'llm';

  return (
    <div className="space-y-5">
      <Panel
        icon={FolderPlus}
        title="保存目录"
        description="给 115 网盘里的常用目录取个好记的名字，之后选择保存位置时直接选名字即可。"
        bodyClassName="p-4 sm:p-5"
      >
        <div className="grid gap-3 md:grid-cols-2">
          {Object.entries(dirs).map(([alias, cid]) => {
            const u = usage(alias);
            const name = dirLabel(text(cid));
            return (
              <div key={alias} className="flex items-center gap-3 rounded-lg border border-border bg-background/60 p-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                  <Folder className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-sm font-medium">{alias}</span>
                    {u.isDefault ? <Tag>默认</Tag> : null}
                    {u.sources ? <Tag muted>{u.sources} 个来源</Tag> : null}
                  </div>
                  <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground" title={`115 目录编号 ${text(cid)}`}>
                    {name === null ? <CircleAlert className="size-3 shrink-0 text-warning" /> : null}
                    <span className="truncate">
                      {name === undefined ? '正在读取目录名称…' : name === null ? '未能读取目录名称' : `115 / ${name}`}
                    </span>
                  </p>
                </div>
                <div className="flex shrink-0 items-center">
                  <Button
                    variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-primary"
                    aria-label={`更换「${alias}」对应的目录`} title="更换目录"
                    onClick={() => setPicker({ alias, cid: text(cid), name: name || alias })}
                  >
                    <FolderSearch />
                  </Button>
                  <Button
                    variant="ghost" size="icon" className="size-8 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    aria-label={`删除目录「${alias}」`} title="删除"
                    onClick={() => removeDir(alias)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
        {Object.values(dirs).some(cid => dirLabel(text(cid)) === null) ? (
          <p className="mt-3 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
            <CircleAlert className="size-3.5 text-warning" />
            部分目录名称读取失败，通常是 115 尚未连接。
            <Link to="/config?tab=connections" className="text-primary hover:underline">去连接 115</Link>
          </p>
        ) : null}

        <div className="mt-4 rounded-lg border border-dashed border-border p-3 sm:p-4">
          <p className="mb-2.5 text-[13px] font-medium">添加目录</p>
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_auto]">
            <Input
              aria-label="新目录名称" placeholder="名字，如：纪录片" className="bg-card"
              value={newAlias} onChange={e => { setNewAlias(e.target.value); setAddError(''); }}
              onKeyDown={e => { if (e.key === 'Enter') addDir(); }}
            />
            <Button
              type="button" variant="outline" className="min-w-0 justify-start bg-card font-normal"
              onClick={() => setPicker({ cid: newDir?.id ?? '0', name: newDir?.name ?? '根目录' })}
            >
              <FolderSearch className="text-primary" />
              <span className={cn('truncate', !newDir && 'text-muted-foreground')}>{newDir ? `115 / ${newDir.name}` : '选择 115 目录'}</span>
            </Button>
            <Button type="button" onClick={addDir}><Plus />添加</Button>
          </div>
          {addError ? <p className="mt-2 text-xs text-destructive">{addError}</p> : null}
        </div>
      </Panel>

      <RuleComposer config={config} dirty={dirty} onApply={applySections} />

      <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
        <Panel icon={ShieldCheck} title="默认处理方式" description="监听来源没有单独设置时，按这里的规则处理新资源。" bodyClassName="p-4 sm:p-5">
          <div className="space-y-5">
            <div className="space-y-1.5">
              <p className="text-[13px] font-medium leading-5">新资源如何处理</p>
              <RadioCards
                label="默认确认方式"
                value={text(defaults.approval_mode)}
                onChange={v => setField('defaults', 'approval_mode', v)}
                options={APPROVAL_OPTIONS.map(v => ({ value: v, label: approvalLabels[v], hint: APPROVAL_HINTS[v] }))}
              />
              <p className="text-xs leading-5 text-muted-foreground">即使选了自动，AI 没把握时仍会转给你确认。</p>
            </div>
            <Field label="默认保存到">
              <Select
                value={text(defaults.save_to)}
                onValueChange={v => setField('defaults', 'save_to', v)}
                options={aliases.map(a => ({ value: a, label: a }))}
                placeholder={aliases.includes(text(defaults.save_to)) ? undefined : '请选择保存目录'}
              />
            </Field>
            <Field
              label="收藏偏好"
              hint={preferencesActive
                ? '用大白话告诉 AI 你想要什么，它会据此辅助判断是否转存。'
                : <>只在转存判断方式为「AI 按偏好判断」时生效；当前默认为「{providerLabel('decision', text(defaults.decision), catalog)}」，可在 <Link to="/extensions" className="text-primary hover:underline">插件 → 核心能力</Link> 中切换。</>}
            >
              <Textarea
                className="min-h-20 bg-card"
                value={text(defaults.preferences)}
                onChange={e => setField('defaults', 'preferences', e.target.value)}
                placeholder="如：只收藏 4K 电影；综艺和广告一律不要；剧集优先内封中字版本"
              />
            </Field>
          </div>
        </Panel>

        <Panel icon={Send} title="识别后转发" description="识别出资源后，把整理好的消息另外转发到指定 Bot 或频道；与是否转存成功无关。" bodyClassName="p-4 sm:p-5">
          <div className="space-y-5">
            <SwitchRow
              title="开启转发"
              description={forward.enabled ? '识别出资源后自动转发一条消息' : '关闭时只转存，不发送转发消息'}
              checked={Boolean(forward.enabled)}
              onCheckedChange={v => setField('forward', 'enabled', v)}
            />
            <Field label="转发到（Bot 或频道）">
              <Input className="bg-card" value={text(forward.target)} onChange={e => setField('forward', 'target', e.target.value)} placeholder="@我的归档频道" />
            </Field>
            <div className="space-y-1.5">
              <Field label="消息样式">
                <Textarea
                  ref={templateRef}
                  className="min-h-28 bg-card font-mono text-xs leading-6 md:text-xs"
                  value={text(forward.template)}
                  onChange={e => setField('forward', 'template', e.target.value)}
                  placeholder={'{title} ({year}) {resolution}\n{size}\n{link}'}
                />
              </Field>
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <span>点击插入：</span>
                {FORWARD_TOKENS.map(([token, label]) => (
                  <button
                    key={token} type="button" onClick={() => insertToken(token)}
                    className="rounded-md border border-border bg-muted/60 px-1.5 py-0.5 transition-colors hover:border-primary/40 hover:text-primary"
                  >
                    {label} <code className="font-mono text-[11px]">{token}</code>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Panel>
      </div>

      {picker ? (
        <DirectoryPicker
          open
          initialCid={picker.cid}
          initialName={picker.name}
          onOpenChange={open => { if (!open) setPicker(null); }}
          onSelect={dir => {
            rememberDir(dir.id, dir.name);
            if (picker.alias) setField('save_dirs', picker.alias, dir.id);
            else { setNewDir(dir); setAddError(''); }
          }}
        />
      ) : null}
    </div>
  );
}
