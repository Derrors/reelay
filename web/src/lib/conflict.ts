/** 整理冲突的两种人工处理：删除本次转存的文件 / 覆盖保存。 */
import { confirmDialog } from './confirm';
import type { IOrganizeConflict } from './types';

export type ConflictChoice = 'skip' | 'replace';

export const CONFLICT_DONE: Record<ConflictChoice, string> = {
  skip: '已排队：删除本次转存的文件',
  replace: '已排队：覆盖保存',
};

/** 两种处理都会把一份文件移进 115 回收站，执行前确认。 */
export function confirmConflict(choice: ConflictChoice, conflict: IOrganizeConflict): Promise<boolean> {
  const existing = conflict.existing.name ?? '（未知文件名）';
  const incoming = conflict.incoming.name ?? '（未知文件名）';
  return confirmDialog(choice === 'skip' ? {
    title: '删除本次转存的文件？',
    description: '保留库里原有的文件，本次转存的这份移进 115 回收站。',
    details: [{ label: '保留', value: existing }, { label: '删除', value: incoming }],
    confirmText: '删除转存文件',
    destructive: true,
  } : {
    title: '覆盖库里的文件？',
    description: '本次转存的文件按命名规则放进库里，原有的那份移进 115 回收站。',
    details: [{ label: '删除', value: existing }, { label: '换成', value: incoming }],
    confirmText: '覆盖保存',
    destructive: true,
  });
}
