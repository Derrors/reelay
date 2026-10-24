import type { ComponentType } from 'react';
import type { Obj } from '@/lib/values';
import { organizerFlowNote } from './organizer/layouts';
import { OrganizerEditor } from './organizer/OrganizerEditor';
import type { PluginEditorProps } from './types';

/** 内置插件在管理台里的专用界面。 */
export interface PluginUi {
  /** 专用设置编辑器（只读写 plugins.<插件名>）。编辑器需要后端数据时调用插件声明的动作（configApi.extensionAction）。 */
  editor?: ComponentType<PluginEditorProps>;
  /** 插件页「运行概览」里插件节点旁的一句话（如当前的目录方式）；section 是插件的设置，stage 是挂载的环节。 */
  flowNote?: (section: Obj, stage: string) => string | undefined;
}

/**
 * 后端插件声明的 ui_config_type → 前端界面，每个插件的界面在 plugins/<插件名>/。
 * 外部插件没有专用界面，用 ui_fields 自动生成表单。
 */
export const PLUGIN_UI: Record<string, PluginUi> = {
  media_library: { editor: OrganizerEditor, flowNote: organizerFlowNote },
};
