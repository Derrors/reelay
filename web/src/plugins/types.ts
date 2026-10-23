import type { Dispatch, SetStateAction } from 'react';

/** 插件设置编辑器只读写自己那一段：plugins.<插件名>。 */
export type Obj = Record<string, unknown>;
export type SetSection = (update: (section: Obj) => Obj) => void;
export interface PluginEditorProps { section: Obj; setSection: SetSection }
/** 设置页传给编辑器外壳的完整配置。 */
export interface PluginConfigProps { config: Obj; setConfig: Dispatch<SetStateAction<Obj>> }

export const obj = (v: unknown): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : {});
export const at = (root: Obj, key: string) => obj(root[key]);
export const text = (v: unknown) => (v == null ? '' : String(v));
