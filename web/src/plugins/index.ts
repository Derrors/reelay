import type { ComponentType } from 'react';
import { OrganizerEditor } from './organizer/OrganizerEditor';
import type { PluginEditorProps } from './types';

/**
 * 内置插件的专用设置编辑器：后端插件声明的 ui_config_type → 前端组件，每个插件的界面在 plugins/<插件名>/。
 * 编辑器需要后端数据时调用插件声明的动作（api.extensionAction）。外部插件没有专用编辑器，
 * 用 ui_fields 自动生成表单。
 */
export const PLUGIN_EDITORS: Record<string, ComponentType<PluginEditorProps>> = {
  media_library: OrganizerEditor,
};
