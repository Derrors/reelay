import type { Dispatch, SetStateAction } from 'react';
import type { Obj } from '@/lib/values';

/** 插件设置编辑器只读写自己那一段：plugins.<插件名>。 */
export type SetSection = (update: (section: Obj) => Obj) => void;
export interface PluginEditorProps { section: Obj; setSection: SetSection }
/** 设置页传给编辑器外壳的完整配置。 */
export interface PluginConfigProps { config: Obj; setConfig: Dispatch<SetStateAction<Obj>> }
