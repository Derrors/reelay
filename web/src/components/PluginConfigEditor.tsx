import { PLUGIN_EDITORS } from '@/plugins';
import { at, type Obj, type PluginConfigProps } from '@/plugins/types';
import type { IExtension } from '@/lib/types';

/** 插件的专用设置编辑器（按 config_type 查 plugins/ 里的组件）；编辑器只读写 plugins.<插件名> 这一段。 */
export default function PluginConfigEditor({ extension, config, setConfig }: { extension: IExtension } & PluginConfigProps) {
  const Editor = extension.config_type ? PLUGIN_EDITORS[extension.config_type] : undefined;
  if (!Editor) return null;
  const section = at(at(config, 'plugins'), extension.name);
  const setSection = (update: (section: Obj) => Obj) => setConfig(c => {
    const plugins = at(c, 'plugins');
    return { ...c, plugins: { ...plugins, [extension.name]: update(at(plugins, extension.name)) } };
  });
  return <Editor section={section} setSection={setSection} />;
}
