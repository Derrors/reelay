import { ConfigFrame } from './ConfigFrame';
import { ExtensionsTab } from './ExtensionsTab';
import { PLUGIN_KEYS, sortHooks } from './shared';
import { useConfigDraft } from './useConfigDraft';

/** 插件：各环节的核心能力（providers、defaults 里的实现）与插件在 plugins.<名> 下的设置。开关立即生效，参数修改需保存。 */
export default function PluginsPage() {
  const draft = useConfigDraft(PLUGIN_KEYS);
  const catalog = draft.data?.extensions;
  // 同名扩展（多个挂载点或事件）后端已合并为一项
  const hooks = sortHooks(catalog?.hooks ?? []);
  const events = catalog?.events ?? [];
  const dirtyLabels = [
    ...(['providers', 'defaults'].some(draft.changed) ? ['核心能力'] : []),
    ...[...hooks, ...events].filter(x => draft.changed(x.config_section)).map(x => x.label),
  ];

  return (
    <ConfigFrame
      draft={draft}
      title="插件"
      description="选择各环节的实现方式，开关附加功能并调整参数。开关立即生效，参数修改需保存。"
      dirtyLabels={dirtyLabels}
      onSave={() => void draft.save(`${dirtyLabels.join('、') || '设置'}已保存，新任务将按新设置执行`)}
    >
      {catalog && draft.data ? (
        <ExtensionsTab
          catalog={catalog} hooks={hooks} events={events}
          config={draft.config} saved={draft.data.config} setConfig={draft.setConfig} setField={draft.setField}
          busy={draft.busy} onToggle={(x, v) => void draft.toggleExtension(x, v)} onRestore={draft.restore}
        />
      ) : null}
    </ConfigFrame>
  );
}
