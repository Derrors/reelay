import { useSearchParams } from 'react-router-dom';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { BasicTab } from './BasicTab';
import { ConfigFrame } from './ConfigFrame';
import { ConnectionsTab } from './ConnectionsTab';
import { ProxyTab } from './ProxyTab';
import { RecognitionTab } from './RecognitionTab';
import { ServicesTab, SystemTab, TelegramTab } from './ServiceTabs';
import { CONFIG_SECTIONS, SYSTEM_KEYS } from './shared';
import { SourcesTab } from './SourcesTab';
import { useConfigDraft } from './useConfigDraft';

const TABS = [
  { value: 'common', label: '基础' },
  { value: 'sources', label: '监听来源' },
  { value: 'advanced', label: '识别与确认' },
  { value: 'services', label: 'AI 与 TMDB' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'connections', label: '115 网盘' },
  { value: 'proxy', label: '网络代理' },
  { value: 'system', label: '运行与安全' },
];
const TRIGGER_CLASS = [
  'h-9 gap-1.5 rounded-md px-2 text-[13px] text-muted-foreground sm:h-8 sm:px-3.5',
  'data-[state=active]:bg-card data-[state=active]:text-foreground data-[state=active]:shadow-xs',
  'dark:text-muted-foreground dark:data-[state=active]:border-transparent dark:data-[state=active]:bg-card dark:data-[state=active]:text-foreground',
].join(' ');

/** 设置：系统配置的各个分区，按页签组织，修改后在页面底部统一保存。 */
export default function SettingsPage() {
  const [search, setSearch] = useSearchParams();
  const requested = search.get('tab');
  const tab = TABS.find(t => t.value === requested)?.value ?? 'common';
  const setTab = (value: string) => setSearch(prev => {
    const next = new URLSearchParams(prev);
    next.set('tab', value);
    return next;
  }, { replace: true });

  const draft = useConfigDraft(SYSTEM_KEYS);
  const { data, config, setField, update } = draft;
  const dirtySections = CONFIG_SECTIONS.filter(s => s.paths.some(draft.changed));
  const dirtyTabs = new Set(dirtySections.map(s => s.tab));
  const catalog = data?.extensions ?? null;
  const secretProps = data ? { secrets: data.secrets, onSecrets: draft.setSecrets } : null;

  return (
    <ConfigFrame
      draft={draft}
      title="设置"
      description="系统配置：保存目录、监听来源、确认方式、AI 与 TMDB、Telegram、115 网盘、网络代理与访问安全。修改后在页面底部保存，与配置文件同步。"
      dirtyLabels={dirtySections.map(s => s.label)}
      onSave={() => void draft.save('设置已保存并立即生效')}
    >
      {data && secretProps ? (
        <Tabs value={tab} onValueChange={setTab} className="gap-5">
          <TabsList className="grid h-auto w-full grid-cols-4 rounded-lg bg-muted p-1 md:inline-flex md:w-fit">
            {TABS.map(t => (
              <TabsTrigger key={t.value} value={t.value} className={TRIGGER_CLASS}>
                {t.label}
                {dirtyTabs.has(t.value) ? <span className="size-1.5 rounded-full bg-warning" aria-label="有未保存的修改" /> : null}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="common">
            <BasicTab
              config={config} catalog={catalog} update={update} setField={setField}
              dirNames={draft.dirNames} rememberDir={draft.rememberDir}
              dirty={draft.dirty} applySections={draft.applySections}
            />
          </TabsContent>
          <TabsContent value="sources">
            <SourcesTab config={config} catalog={catalog} update={update} />
          </TabsContent>
          <TabsContent value="advanced">
            <RecognitionTab config={config} setField={setField} />
          </TabsContent>
          <TabsContent value="services">
            <ServicesTab config={config} saved={data.config} setField={setField} {...secretProps}
              pendingSecrets={draft.pendingSecrets} setPendingSecret={draft.setPendingSecret} />
          </TabsContent>
          <TabsContent value="telegram">
            <TelegramTab config={config} setField={setField} {...secretProps} />
          </TabsContent>
          <TabsContent value="connections">
            <ConnectionsTab connections={data.connections} config={config} setField={setField} />
          </TabsContent>
          <TabsContent value="proxy">
            <ProxyTab config={config} saved={data.config} setField={setField} {...secretProps}
              pendingSecrets={draft.pendingSecrets} setPendingSecret={draft.setPendingSecret} />
          </TabsContent>
          <TabsContent value="system">
            <SystemTab config={config} setField={setField} {...secretProps} />
          </TabsContent>
        </Tabs>
      ) : null}
    </ConfigFrame>
  );
}
