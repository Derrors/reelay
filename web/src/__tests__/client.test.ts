import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, apiUrl, ApiError } from '@/api/client';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiUrl', () => {
  it('拼接 BASE_URL 与接口路径，并去掉多余的前导斜杠', () => {
    expect(apiUrl('dashboard')).toBe('/api/dashboard');
    expect(apiUrl('/events')).toBe('/api/events');
  });
});

describe('request 错误处理', () => {
  const respond = (status: number, body: unknown) =>
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })));

  it('使用后端 detail 作为错误文案并保留状态码', async () => {
    respond(409, { detail: '任务不是待审批状态' });
    await expect(api.dashboard()).rejects.toMatchObject({ status: 409, message: '任务不是待审批状态' });
  });

  it('把 FastAPI 校验错误数组拼成可读文案', async () => {
    respond(422, { detail: [{ loc: ['body', 'password'], msg: 'Field required' }] });
    const error = await api.dashboard().catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe('body.password：Field required');
  });

  it('非 JSON 错误响应回退到通用文案', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>bad gateway</html>', { status: 502 })));
    await expect(api.dashboard()).rejects.toMatchObject({ status: 502, message: '请求失败（502）' });
  });
});
