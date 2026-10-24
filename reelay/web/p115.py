"""115 网盘：登录状态、cookie 录入与扫码、开放平台授权、探活，以及管理台选目录用的目录浏览。"""
from __future__ import annotations

import asyncio
import io

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core import auth115
from ..core.auth115 import AuthState
from ..providers.storage import StorageBrowseError


class CookiePayload(BaseModel):
    cookies: str


def _qr_svg(url: str) -> bytes:
    import qrcode
    from qrcode.image.svg import SvgPathImage
    output = io.BytesIO()
    qrcode.make(url, image_factory=SvgPathImage).save(output)
    return output.getvalue()


def _qr_response(session_of, uid: str) -> Response:
    """扫码会话的二维码（按 115 返回的链接在本地生成）；会话不存在或过期时 404。"""
    try:
        session = session_of(uid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=_qr_svg(session.get("qrcode_url") or f"https://115.com/scan/dg-{uid}"),
                    media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


async def _call(function, *args, failure: str):
    """在线程里调用 115（会阻塞）；扫码会话过期为 404，其余失败为 502 并说明原因。"""
    try:
        return await asyncio.to_thread(function, *args)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 115 侧的失败原样告诉用户
        raise HTTPException(status_code=502, detail=f"{failure}：{exc}") from exc


def router(service) -> APIRouter:
    api = APIRouter()

    @api.get("/api/auth115/status")
    async def status():
        return auth115.get_store().status()

    @api.post("/api/auth115/ping")
    async def ping():
        store = auth115.get_store()
        cookie_state, oauth_state = await asyncio.gather(asyncio.to_thread(store.ping_cookie, True),
                                                         asyncio.to_thread(store.ping_oauth, True))
        return {"cookie": cookie_state, "oauth": oauth_state, "status": store.status()}

    @api.put("/api/auth115/cookie")
    async def set_cookie(payload: CookiePayload):
        """先用这组 cookie 探活，确认能用才保存，失效的 cookie 不会顶掉原来能用的那组。"""
        cookies = (payload.cookies or "").strip()
        if not cookies:
            raise HTTPException(status_code=422, detail="cookie 不能为空")
        if "UID" not in cookies.upper() or "CID" not in cookies.upper():
            raise HTTPException(status_code=422, detail="不像有效的 115 cookie（需包含 UID/CID）")
        store = auth115.get_store()
        state, error = await asyncio.to_thread(store.probe_cookies, cookies)
        if state == AuthState.EXPIRED:
            raise HTTPException(status_code=409, detail="这组 cookie 已失效，没有保存，请重新获取")
        if state != AuthState.ACTIVE:
            raise HTTPException(status_code=409, detail=f"连不上 115 验证这组 cookie，没有保存：{error[:100]}")
        await asyncio.to_thread(store.set_cookies, cookies)
        return {"ok": True, "state": await asyncio.to_thread(store.ping_cookie, True)}

    @api.post("/api/auth115/cookie/qr")
    async def cookie_qr_start():
        return await _call(auth115.get_store().cookie_qr_start, failure="申请 Cookie 登录二维码失败")

    @api.get("/api/auth115/cookie/qr")
    async def cookie_qr_status(uid: str = Query(...)):
        return await _call(auth115.get_store().cookie_qr_status, uid, failure="查询 Cookie 扫码状态失败")

    @api.get("/api/auth115/cookie/qr/image")
    async def cookie_qr_image(uid: str = Query(...)):
        return _qr_response(auth115.get_store().cookie_qr_session, uid)

    @api.post("/api/auth115/oauth/qr")
    async def oauth_qr_start():
        return await _call(auth115.get_store().qr_start, failure="申请授权二维码失败")

    @api.get("/api/auth115/oauth/qr")
    async def oauth_qr_status(uid: str = Query(...)):
        return await _call(auth115.get_store().qr_status, uid, failure="查询扫码状态失败")

    @api.get("/api/auth115/oauth/qr/image")
    async def oauth_qr_image(uid: str = Query(...)):
        """授权二维码按开放平台返回的链接在本地生成；不能用网页登录的二维码图片接口，那是另一种登录。"""
        return _qr_response(auth115.get_store().qr_session, uid)

    @api.delete("/api/auth115/oauth")
    async def oauth_clear():
        await asyncio.to_thread(auth115.get_store().clear_oauth)
        return {"ok": True}

    @api.get("/api/storage/dirs")
    async def storage_dirs(cid: str = Query(default="0", pattern=r"^[0-9]+$"), offset: int = Query(default=0, ge=0),
                           limit: int = Query(default=100, ge=1, le=1000)):
        """选目录：列出 cid 下的子目录（默认存储 Provider 的网盘）；错误不伪装成空目录。"""
        cfg = service.pipeline.cfg
        storage = service.pipeline.reg.get_provider("storage", cfg["defaults"]["storage"])
        if not hasattr(storage, "browse"):
            raise HTTPException(status_code=409, detail=f"存储 {storage.name} 不支持浏览目录")
        try:
            return await asyncio.to_thread(storage.browse, cid, offset, limit)
        except StorageBrowseError as exc:
            return JSONResponse(status_code=exc.status, content={"detail": str(exc), "code": exc.code})

    return api
