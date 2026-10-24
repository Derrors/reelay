// AI 服务：模型与连通性检测、调用记录、一句话生成规则
import type { IRuleChange } from '@/lib/types';
import { get, post } from './request';

/** 测试 AI 服务连通性的结果。 */
export interface ILlmTest {
  ok: boolean; model: string; seconds: number;
  reply?: string; prompt_tokens?: number | null; completion_tokens?: number | null; error?: string;
}

/** 地址、模型与推理强度用表单里的值（可以还没保存）。密钥默认用已保存的；地址和已保存的不同时
 *  必须带上这个地址的 api_key，否则后端返回 409（密钥属于原来的地址）。 */
export interface ILlmForm { base_url?: string; model?: string; reasoning_effort?: string; api_key?: string }

/** 某个模型支持的推理强度：supported / unsupported（接口明确拒绝）/ unknown（超时等，未能确认）。efforts 为 null 表示没检测过。 */
export interface ILlmEfforts { model: string; efforts: Record<string, 'supported' | 'unsupported' | 'unknown'> | null; note?: string | null }

export interface ILlmUsagePurpose {
  purpose: string;
  calls: number;
  ok: number;
  invalid: number;
  refused: number;
  error: number;
  avg_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface ILlmUsage {
  since: number;
  total: { calls: number; ok: number; invalid: number; refused: number; error: number;
    prompt_tokens: number; completion_tokens: number };
  purposes: ILlmUsagePurpose[];
  daily: Array<{ d: string; calls: number; tokens: number }>;
  models: string[];
}

export interface ICompileResult {
  summary: string;
  unsupported: string[];
  changes: IRuleChange[];
  /** 确认后原样交给 saveSections 的配置块。 */
  sections: Record<string, unknown>;
  revision: string;
}

/** 空的密钥不发：后端据此区分「用已保存的密钥」。 */
const withKey = ({ api_key, ...form }: ILlmForm) => (api_key?.trim() ? { ...form, api_key: api_key.trim() } : form);

export const aiApi = {
  /** 读取已记住的检测结果（不发请求给模型）。base_url 留空也要传：空串表示官方地址，不传才是已保存的地址。 */
  efforts: (form: ILlmForm) =>
    get<ILlmEfforts>(`ai/efforts?${new URLSearchParams({ base_url: form.base_url ?? '', model: form.model ?? '' })}`),
  probeEfforts: (form: ILlmForm) => post<ILlmEfforts>('ai/efforts', withKey(form)),
  models: (form: ILlmForm) => post<{ models: string[] }>('ai/models', withKey(form)),
  test: (form: ILlmForm) => post<ILlmTest>('ai/test', withKey(form)),
  usage: (days = 30) => get<ILlmUsage>(`ai/usage?days=${days}`),
  compileRules: (text: string, scope: string) => post<ICompileResult>('ai/compile-rules', { text, scope }),
};
