/** 读写配置这类「结构不定的 JSON」的小工具：取值时容忍缺失与类型不符，写入时不改动原对象。 */

export type Obj = Record<string, unknown>;

export const obj = (v: unknown): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? v as Obj : {});
export const at = (root: Obj, key: string) => obj(root[key]);
export const rows = (v: unknown): Obj[] => (Array.isArray(v) ? v.map(obj) : []);
export const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
export const text = (v: unknown) => (v === undefined || v === null ? '' : String(v));
export const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
/** 数字输入框：留空时保留空串（交给后端校验报错），否则转成数字。 */
export const numberOrBlank = (raw: string) => (raw === '' ? '' : Number(raw));
/** 去掉值为 undefined 的键：用于「沿用默认」时删除来源上的覆盖项。 */
export const prune = (o: Obj): Obj => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));

export const getPath = (root: Obj, path: string) => path.split('.').reduce<unknown>((v, p) => obj(v)[p], root);
export function setPath(root: Obj, path: string, value: unknown): Obj {
  const [first, ...rest] = path.split('.');
  return { ...root, [first]: rest.length ? setPath(obj(root[first]), rest.join('.'), value) : value };
}
