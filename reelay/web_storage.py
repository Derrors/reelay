"""控制台只读115目录服务：保留长ID、显式分页、错误不伪装空目录。"""
from __future__ import annotations

from urllib.error import HTTPError
from .core.auth115 import _is_auth_error, AUTH_ERRNOS, OPEN_AUTH_CODES


class StorageBrowseError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code, self.status = code, status


def browse_dirs(credentials, cid: str, offset: int = 0, limit: int = 100) -> dict:
    from p115client import check_response
    mode = 'oauth' if credentials.has_oauth else 'cookie'
    try:
        client = credentials.open_client() if mode == 'oauth' else credentials.cookie_client()
        if client is None:
            raise StorageBrowseError('credentials_missing', '尚未配置115凭证，请前往连接状态配置。', 409)
        method = client.fs_files_open if mode == 'oauth' else client.fs_files
        result = check_response(method({'cid': cid, 'offset': offset, 'limit': limit,
                                        'cur': 1, 'show_dir': 1, 'record_open_time': 0}, timeout=15))
        data = result.get('data')
        if not isinstance(data, list):
            raise StorageBrowseError('invalid_response', '115返回的目录数据格式异常，请稍后重试。', 502)
        path = result.get('path') or []
        if path and str(path[-1].get('cid', path[-1].get('file_id', path[-1].get('id', cid)))) != cid:
            raise StorageBrowseError('directory_missing', '目标目录不存在或无权访问，未回退到根目录。', 404)
        breadcrumbs = []
        for node in path:
            node_id = node.get('cid', node.get('file_id', node.get('id')))
            if node_id is None or not str(node_id).isascii() or not str(node_id).isdecimal():
                continue
            node_name = node.get('name', node.get('file_name', node.get('n')))
            breadcrumbs.append({'id': str(node_id), 'name': str(node_name or ('根目录' if str(node_id) == '0' else ''))})
        if cid == '0' and not breadcrumbs:
            breadcrumbs = [{'id': '0', 'name': '根目录'}]
        items = []
        for item in data:
            # web 列表目录无 fid，cid 为目录ID；open 列表 file_category=0。
            if 'file_category' in item:
                is_dir = str(item['file_category']) == '0'
                fid, name = item.get('file_id'), item.get('file_name')
                pid = item.get('parent_id', cid)
            else:
                is_dir = not item.get('fid')
                fid, name = item.get('cid'), item.get('n')
                pid = item.get('pid', cid)
            if is_dir:
                if fid is None or not str(fid).isascii() or not str(fid).isdecimal():
                    raise StorageBrowseError('invalid_response', '115返回了无效目录ID。', 502)
                items.append({'id': str(fid), 'name': str(name or ''), 'pid': str(pid)})
        total = int(result.get('count', offset + len(data)))
        next_offset = offset + len(data) if offset + len(data) < total and data else None
        return {'items': sorted(items, key=lambda x: x['name']), 'cid': cid, 'path': breadcrumbs,
                'next_offset': next_offset, 'total_entries': total, 'via': mode}
    except StorageBrowseError:
        raise
    except Exception as exc:
        msg = str(exc)
        status = getattr(exc, 'code', None) if isinstance(exc, HTTPError) else getattr(getattr(exc, 'response', None), 'status_code', None)
        if _is_auth_error(msg, AUTH_ERRNOS | OPEN_AUTH_CODES):
            raise StorageBrowseError('credentials_expired', '115登录态失效，请在连接状态更新cookie或重新扫码授权。', 409) from exc
        if status in (403, 405, 429):
            raise StorageBrowseError('upstream_restricted', f'115上游接口拒绝请求（HTTP {status}），不是空目录。请稍后重试或在连接状态检查授权；不会自动重试绕过限制。') from exc
        if isinstance(exc, TimeoutError) or 'timed out' in msg.lower():
            raise StorageBrowseError('upstream_timeout', '115目录查询超时，请稍后重试。', 504) from exc
        raise StorageBrowseError('upstream_unavailable', '115目录服务暂不可用，请检查网络与授权后重试。', 502) from exc
