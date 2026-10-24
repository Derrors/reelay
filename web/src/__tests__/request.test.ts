import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiUrl, ApiError, appUrl, get, onUnauthorized, query } from '@/api/request';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiUrl', () => {
  it('拼接 BASE_URL 与接口路径，并去掉多余的前导斜杠', () => {
    expect(apiUrl('dashboard')).toBe('/api/dashboard');
    expect(apiUrl('/events')).toBe('/api/events');
    expect(appUrl('mcp')).toBe('/mcp');
  });
});

describe('query', () => {
  it('跳过空值，没有参数时为空串', () => {
    expect(query({ state: 'failed,done', query: '', limit: 50, offset: undefined })).toBe('?state=failed%2Cdone&limit=50');
    expect(query({ q: null })).toBe('');
  });
});

describe('request 错误处理', () => {
  const respond = (status: number, body: unknown) =>
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })));

  it('使用后端 detail 作为错误文案并保留状态码', async () => {
    respond(409, { detail: '任务不是待审批状态' });
    await expect(get('dashboard')).rejects.toMatchObject({ status: 409, message: '任务不是待审批状态' });
  });

  it('把 FastAPI 校验错误数组拼成可读文案', async () => {
    respond(422, { detail: [{ loc: ['body', 'password'], msg: 'Field required' }, { loc: ['body'], msg: '格式错误' }] });
    const error = await get('dashboard').catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe('password：Field required；格式错误');
  });

  it('401 时通知登录已失效，登录接口的 401（密码错误）不算', async () => {
    const listener = vi.fn();
    const off = onUnauthorized(listener);
    respond(409, { detail: 'x' });
    await get('dashboard').catch(() => undefined);
    expect(listener).not.toHaveBeenCalled();
    respond(401, { detail: '密码错误' });
    await expect(get('auth/login')).rejects.toMatchObject({ status: 401, message: '密码错误' });
    expect(listener).not.toHaveBeenCalled();
    respond(401, { detail: '未登录' });
    await expect(get('dashboard')).rejects.toMatchObject({ status: 401, message: '登录已失效，请重新登录控制台' });
    expect(listener).toHaveBeenCalledTimes(1);
    off();
  });

  it('非 JSON 错误响应回退到通用文案', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>bad gateway</html>', { status: 502 })));
    await expect(get('dashboard')).rejects.toMatchObject({ status: 502, message: '请求失败（502）' });
  });

  it('连不上服务时给出中文提示，状态码为 0', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    await expect(get('dashboard')).rejects.toMatchObject({ status: 0, message: '无法连接到服务，请检查网络后重试' });
  });
});
