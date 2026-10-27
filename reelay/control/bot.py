"""控制 Bot —— 可观测与人工干预。

用你自己的 Telegram Bot（管理台「设置」中填写 Bot Token）作为控制入口，提供：

- 命令：``/status``（队列积压、各状态计数、在线状态）、``/pending``（待审批与待人工，点开卡片处理，可一键全部批准
  或忽略）、``/failed``（失败任务）、``/subs``（追剧订阅，可取消或继续订阅）、``/ignored``（不再接收的作品，可恢复）、
  ``/retry <task_id>``、``/help``；列表与修正识别见 ``control/manage.py``，不依赖 AI
- 对话：不以 ``/`` 开头的文字交给对话助手（``control/assistant.py``），如「我有哪些 4K 日本动画」
  「把《X》这几集都批了，存到动漫」；写操作先回显要执行的动作，点「执行」后才执行
- 卡片按钮回调（按钮数据只带短 ID，用 ``messages.callback`` 拼接、``parse_callback`` 解析），见 ``callback``：审批 ``ok`` / ``no`` / ``dir``（换目录）/
  ``to``（存到某个目录）/ ``sub``（追剧：订阅这一季，还在待审批时这一集一并转存）/ ``blk`` → ``bly``（不再接收，先确认再执行）/
  ``back``；需要处理的任务 ``rc``（核验转存）/ ``ro``（重新整理）/ ``rt``（重试）/
  ``ap``（按已到的文件继续）/ ``cf`` → ``cy``（删除转存文件、覆盖保存、放弃并删除，先确认再执行）/ ``pb``；
  修正识别 ``fx``（发出提示，回复它写要改的项）/ ``rp``（重新识别）/ ``fxn``；列表 ``cd``（重新发出卡片）/ ``pl`` ``fl``（翻页）/
  ``pa`` ``pr`` → ``pay`` ``pry``（全部批准、全部忽略：先列出计划，确认时只处理计划里的任务）/ ``us`` → ``usy``（取消订阅）/
  ``so``（继续订阅）/ ``ui``（恢复接收）；订阅建议 ``ss``（订阅）/ ``sd``（不用了）；对话助手的确认按钮 ``do`` / ``cancel``；
  其他前缀交给声明了 ``bot_action:<前缀>`` 能力的扩展（如缺集追更提醒上的按钮）
- 对外暴露 ``send(...)``：给 NotifyProvider 用来推确认卡片（带按钮）与结果通知
- 审批卡片的同步：发出时记下消息（``send(card=...)``），每 ``CARD_SYNC_SEC`` 秒检查一次，任务在管理台、对话助手、
  批量操作或「不再接收」里处理掉后，把卡片改成处理结果并去掉审批按钮，见 ``sync_cards``
- 与 Telegram 断开后在进程里重新连接（见 ``run_forever`` 与 ``core/telegram.py``）；启动时连不上也不退出，稍后再连

未配置 token（dry-run / 未接入）时，本模块不启动，通知走 ConsoleNotify 打印。
消息只发给 ``telegram.owner_id``（你自己的数字 id），不广播。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from ..core import ignored, messages, paths, proxy, secret_store, subscriptions, telegram
from ..core.contracts import TaskState
from ..core.messages import parse_callback, short_key
from ..core.service import ConflictError, ManagementService, NotFoundError
from . import manage
from .assistant import Assistant

log = logging.getLogger("reelay.control.bot")

# 图片说明的长度上限是 1024；留出余量给审批后在末尾追加的结果行
CAPTION_LIMIT = 960

CORRECTION_TTL = 600  # 点「修正」后多久内发的消息当作修正内容
CARD_SYNC_SEC = 5  # 多久检查一次在别处处理过的审批卡片
RETRY_SECONDS = 30  # 连不上 Telegram 时多久重试一次
_CORRECTION_RE = re.compile(re.escape(messages.CORRECTION_MARK.strip()) + r"\s*([0-9a-zA-Z_-]{4,64})")


class ControlBot:
    def __init__(self, service: ManagementService):
        self.service = service
        self.assistant = Assistant(service)
        # 在管理台「设置」填写；改了要重启主进程
        self._token = secret_store.get("control_bot_token")
        self._owner = service.pipeline.cfg["telegram"]["owner_id"] or None
        self._client = None
        self._Button = None
        self._correcting: dict[str, tuple[str, float]] = {}  # 会话 → (正在修正的任务, 过期时间)
        self._card_lock = asyncio.Lock()  # 点按钮改卡片与同步卡片不交错，点出的结果不会被同步覆盖
        self._problem: str | None = None  # 连不上 Telegram 的原因，连上后清空

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._owner)

    async def start(self) -> None:
        if not self.enabled:
            log.info("控制 bot 未配置（缺 token/owner），跳过；通知走控制台")
            return
        from telethon import Button  # 延迟导入
        self._Button = Button
        try:
            await self._open()
        except Exception as exc:
            if not telegram.transient(exc):
                raise
            self._problem = telegram.describe(exc)
            log.warning("控制 bot 暂时连不上 Telegram（%s），稍后自动重试", self._problem)
            return
        log.info("控制 bot 已上线")
        try:
            from telethon import functions, types
            await self._client(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(), lang_code="",
                commands=[types.BotCommand(command=name, description=text) for name, text in manage.COMMAND_MENU]))
        except Exception as exc:  # noqa: BLE001 - 命令菜单只是方便输入
            log.warning("设置控制 bot 命令菜单失败：%s", exc)

    async def _open(self) -> None:
        """新建客户端、登记命令与按钮的处理，用 Bot Token 登录（断开后重新连接也走这里）。"""
        from telethon import TelegramClient, events  # 延迟导入
        api_id = int(secret_store.get("telegram_api_id") or 0)
        api_hash = secret_store.get("telegram_api_hash") or ""
        self._client = TelegramClient(str(paths.CONTROL_BOT_SESSION), api_id, api_hash, proxy=proxy.telethon(),
                                      **telegram.CLIENT_OPTIONS)
        self._register_handlers(events)
        try:
            await asyncio.wait_for(self._client.start(bot_token=self._token), telegram.CONNECT_TIMEOUT)
        except Exception:
            await self._client.disconnect()
            raise
        self._problem = None

    def _register_handlers(self, events) -> None:
        c = self._client

        views = {"pending": manage.pending_list, "failed": manage.failed_list, "subs": manage.subs_list,
                 "ignored": manage.ignored_list, "status": manage.status_view, "help": manage.help_view,
                 "start": manage.help_view}

        @c.on(events.NewMessage(pattern=r"^/(pending|failed|subs|ignored|status|help|start)\b"))
        async def _list(e):
            if not self._is_owner(e):
                return
            text, buttons = views[e.pattern_match.group(1)](self.service)
            await e.reply(text, buttons=self._buttons(buttons), parse_mode="html")

        @c.on(events.NewMessage(pattern=r"/retry (\w+)"))
        async def _retry(e):
            if not self._is_owner(e):
                return
            tid = e.pattern_match.group(1)
            try:
                await self.service.retry(tid)
                await e.reply("♻️ 已重新入队")
            except (ConflictError, NotFoundError):
                await e.reply("未找到可重试的失败任务")

        @c.on(events.NewMessage(incoming=True, func=lambda e: bool(e.raw_text) and not e.raw_text.startswith("/")))
        async def _chat(e):
            if not self._is_owner(e):
                return
            reply = await e.get_reply_message() if getattr(e, "is_reply", False) else None
            task_id = self.correction_target(str(e.chat_id), reply.raw_text if reply else None)
            if task_id:
                text, buttons, photo = await self.correct(task_id, e.raw_text)
                card = task_id if self._pending(task_id) else None  # 修正后仍待审批：发出的是新的审批卡片
                await self.send(text, buttons, chat=e.chat_id, html=True, photo=photo, card=card)
                return
            text, buttons = await self.chat(e.raw_text, chat=str(e.chat_id))
            await e.reply(text, buttons=self._buttons(buttons))

        @c.on(events.CallbackQuery())
        async def _cb(e):
            if str(e.sender_id) != str(self._owner):
                return
            data = (e.data or b"").decode()
            action, args = parse_callback(data)
            if action in ("do", "cancel"):
                await e.edit(await self.confirm(data))
                return
            sent = await self.open(data, chat=str(e.chat_id))
            if sent is not None:  # 发一条新消息（卡片、修正提示），原消息不动
                text, buttons, photo, toast = sent
                if text:
                    card = args[0] if action == "cd" and self._pending(args[0]) else None
                    await self.send(text, buttons, chat=e.chat_id, html=True, photo=photo, card=card)
                await e.answer(toast or "")
                return
            from telethon.errors import MessageNotModifiedError
            from telethon.extensions import html as tl_html
            async with self._card_lock:
                message = await e.get_message()
                original = tl_html.unparse(message.message or "", message.entities or []) if message else ""
                text, buttons, toast = await self.callback(data, original)
                try:
                    if text is None:
                        if buttons:  # 只换按钮，正文不动
                            from telethon import functions
                            await self._client(functions.messages.EditMessageRequest(
                                peer=await e.get_input_chat(), id=e.message_id,
                                reply_markup=self._client.build_reply_markup(self._buttons(buttons))))
                    else:
                        await e.edit(text, buttons=self._buttons(buttons) or None, parse_mode="html")
                except MessageNotModifiedError:
                    toast = toast or "已是最新"  # 如「刷新」时内容没变：Telegram 拒绝原样编辑，不算出错
                # 卡片上点出了结果（批准、忽略……）就不再同步它
                self.service.store.card_drop(str(e.chat_id), e.message_id, settled_only=True)
            await e.answer(toast or "")

    async def callback(self, data: str, original: str = "") -> tuple[str | None, list | None, str | None]:
        """卡片按钮：返回 (新正文，None 表示不改；新按钮；顶部提示)。original 是卡片当前的正文（HTML）。

        执行后正文原样保留、末尾加一行结果，按钮只留链接（如 TMDB 详情）。
        """
        action, args = parse_callback(data)
        listed = await self.manage(action, args, original)
        if listed is not None:
            return listed
        task_id, arg = (args + ["", ""])[:2]
        ctx = self.service.store.get_task(task_id) if task_id else None
        links = messages.link_buttons(ctx) if ctx else []
        card = original or (messages.approval_card(ctx) if ctx else f"<code>{messages.esc(task_id)}</code>")
        pending = bool(ctx and ctx.state == TaskState.PENDING_APPROVAL)
        if action == "sub" and ctx:  # 待审批的卡片上，以及批准之后的卡片上
            return await self._subscribe(ctx, card, links)
        # 「忽略」也出现在失败任务的提醒上：那时是不再处理（见下方 operations）
        if action in ("ok", "to", "dir", "back", "blk", "bly") or (action == "no" and pending):
            if not pending:  # 已在别处处理
                return (messages.approval_result(card, "stale", ctx.state.value if ctx else None),
                        self._result_buttons(ctx) if ctx else links, None)
            if action == "dir":
                return None, messages.directory_buttons(task_id, list(self.service.pipeline.cfg["save_dirs"])), None
            if action == "back":
                return None, self._approval_buttons(ctx), None
            if action == "blk":
                return None, messages.block_confirm_buttons(ctx), "确认后，这部作品之后的消息都会直接忽略；可以用 /ignored 恢复"
            if action == "bly":
                return await self._ignore_work(task_id, card, links)
            text = await self.decide(data, card)
            return text, self._result_buttons(self.service.store.get_task(task_id) or ctx), None
        if action == "cf" and ctx and arg in messages.CONFLICT_CHOICES:
            return None, messages.confirm_buttons(task_id, arg), messages.CONFLICT_CHOICES[arg][1]
        if action == "pb" and ctx:
            return None, messages.problem_buttons(ctx), None
        if action == "fxn":
            self._correcting = {chat: item for chat, item in self._correcting.items() if item[0] != task_id}
            return "✏️ 已取消修正，没有修改。", None, None
        if action == "rp" and ctx:
            self._correcting = {chat: item for chat, item in self._correcting.items() if item[0] != task_id}
            try:
                await self.service.reparse(task_id)
            except (ConflictError, NotFoundError, ValueError) as exc:
                return None, None, str(exc)[:190]
            return "🔄 已重新识别，识别完会发来新的卡片。", None, "已重新识别"
        operations = {
            "rc": (lambda: self.service.reconcile_transfer(task_id), "🔍 已排队核验转存，结果另行通知"),
            "ro": (lambda: self.service.requeue_organize(task_id), "📂 已排队重新整理，结果另行通知"),
            "rt": (lambda: self.service.retry(task_id), "🔁 已重新排队"),
            "no": (lambda: self.service.reject(task_id), "🙈 已忽略，不再处理"),
            "ap": (lambda: self.service.accept_partial_transfer(task_id), "✅ 已排队：按已到的文件继续整理，结果另行通知"),
            "cy": ((lambda: self.service.abandon_transfer(task_id)) if arg == "abandon"
                   else (lambda: self.service.requeue_organize(task_id, conflict=arg)),
                   f"{'♻️' if arg == 'replace' else '🗑'} 已排队{messages.CONFLICT_CHOICES.get(arg, ('',))[0]}，结果另行通知"),
        }
        if action not in operations:
            handled = await self._extension(action, args, original)
            return handled if handled is not None else (None, links, "这个按钮已失效")
        if action == "cy" and arg not in messages.CONFLICT_CHOICES:
            return None, links, "这个按钮已失效"
        run, done = operations[action]
        try:
            await run()
        except (ConflictError, NotFoundError, ValueError) as exc:
            return messages.with_result(card, f"ℹ️ {messages.esc(exc)}"), links, None
        return messages.with_result(card, done), links, None

    # ------------------------------------------------------------------ #
    # 列表、修正与追更（control/manage.py）
    # ------------------------------------------------------------------ #
    async def open(self, data: str, *, chat: str = "owner") -> tuple[str | None, list | None, str | None, str] | None:
        """要另发一条消息的按钮：``cd`` 重新发出任务卡片，``fx`` 发出修正提示。返回 (正文, 按钮, 配图, 顶部提示)；
        其余按钮返回 None，交给 callback 在原消息上改。"""
        action, args = parse_callback(data)
        if action not in ("cd", "fx") or not args:
            return None
        task_id = args[0]
        ctx = self.service.store.get_task(task_id)
        if not ctx:
            return None, None, None, "任务不存在"
        if action == "cd":
            text, buttons, photo = messages.task_card(
                ctx, subscribed=subscriptions.subscribed(self.service.store.subscriptions(), ctx))
            return text, buttons, photo, ""
        if not messages.correctable(ctx):
            return None, None, None, f"现在不能修正（当前：{ctx.state.value}）"
        self._correcting[chat] = (task_id, time.time() + CORRECTION_TTL)
        return messages.correction_prompt(ctx), messages.correction_buttons(task_id), None, "发一句要改的内容"

    def correction_target(self, chat: str, replied: str | None) -> str | None:
        """这条消息是不是修正内容：回复了修正提示，或点「修正」后十分钟内发的第一条消息。用过一次就不再等修正。"""
        task_id, expires = self._correcting.pop(chat, (None, 0))
        match = _CORRECTION_RE.search(replied or "")
        if match:
            return match.group(1)
        return task_id if task_id and expires > time.time() else None

    async def correct(self, task_id: str, text: str) -> tuple[str, list | None, str | None]:
        """按回复的一句话修正识别结果，发回修正后的卡片（待审批的重新核对 TMDB）。"""
        ctx = self.service.store.get_task(task_id)
        if not ctx:
            return "任务不存在", None, None
        try:
            patch = manage.parse_correction(text)
        except ValueError as exc:
            patch, problem = {}, str(exc)
        else:
            problem = "没看出要改什么"
        if not patch:
            return (f"ℹ️ {messages.esc(problem)}。再点一次「修正」，写成「片名 繁花」「第 2 季 E01-E05」"
                    "「电影 2023」这样。", None, None)
        before = ctx.parsed
        try:
            await self.service.correct_parsed(task_id, patch)
        except (ConflictError, NotFoundError, ValueError) as exc:
            return f"ℹ️ 没有修改：{messages.esc(exc)}", None, None
        ctx = self.service.store.get_task(task_id)
        text, buttons, photo = messages.task_card(ctx, subscribed=subscriptions.subscribed(self.service.store.subscriptions(), ctx))
        changes = messages.esc(manage.describe_changes(before, ctx.parsed))
        return messages.with_result(text, f"✏️ <b>已修正</b>：{changes}"), buttons, photo

    async def manage(self, action: str, args: list[str], original: str = "") -> tuple[str | None, list | None, str | None] | None:
        """列表、订阅与扩展的按钮（不绑定单条任务）；不是这些按钮时返回 None。"""
        service = self.service
        first = args[0] if args else ""
        if action in ("pl", "fl"):
            offset = int(first) if first.isdigit() else 0
            text, buttons = (manage.pending_list if action == "pl" else manage.failed_list)(service, offset)
            return text, buttons, None
        if action in ("pa", "pr"):
            text, buttons = manage.bulk_confirm(service, action)
            return text, buttons, None
        if action in ("pay", "pry"):
            return await manage.bulk_run(service, first), [[("📋 查看待处理", messages.callback("pl", 0))]], None
        if action in ("us", "usy", "so"):
            item = manage.find_subscription(service, first)
            if not item:
                return None, None, "这条订阅已经不在了"
            name = f"《{messages.esc(item['title'])}》第 {item['season']} 季"
            if action == "us":
                return (f"⚠️ 取消订阅{name}？之后的新集不再自动转存，也不再巡检缺集。",
                        [[("确认取消订阅", messages.callback("usy", item["id"])), ("↩ 返回", messages.callback("sl"))]], None)
            try:
                if action == "usy":
                    await service.remove_subscription({"id": item["id"]})
                else:
                    service.reopen_subscription({"id": item["id"]})
            except (ConflictError, NotFoundError, ValueError) as exc:
                return None, None, str(exc)[:190]
            text, buttons = manage.subs_list(service)
            done = f"🗑 已取消订阅{name}" if action == "usy" else f"▶️ 已继续订阅{name}，新集照常自动转存"
            return f"{done}\n\n{text}", buttons, None
        if action in ("ss", "sd"):
            return await self._suggestion(action, args, original)
        if action in ("sl", "st"):
            text, buttons = (manage.subs_list if action == "sl" else manage.status_view)(service)
            return text, buttons, None
        if action == "ui":
            item = manage.find_ignored(service, first)
            if not item:
                return None, None, "这条记录已经不在了"
            try:
                await service.unignore_work({"id": item["id"]})
            except (ConflictError, NotFoundError, ValueError) as exc:
                return None, None, str(exc)[:190]
            text, buttons = manage.ignored_list(service)
            return f"↩️ 已恢复接收《{messages.esc(item['title'])}》\n\n{text}", buttons, None
        return None

    async def _extension(self, action: str, args: list[str], original: str):
        """扩展声明的按钮（能力 ``bot_action:<前缀>``，如缺集追更提醒上的按钮）；没有扩展处理时返回 None。"""
        pipeline = self.service.pipeline
        handle = pipeline.reg.capability(f"bot_action:{action}", pipeline.cfg)
        if not handle:
            return None
        with pipeline.scope():
            return handle(action, args[0] if args else "", original)

    async def _suggestion(self, action: str, args: list[str], original: str) -> tuple[str | None, list | None, str | None]:
        """订阅建议的「订阅」「不用了」：推送来的建议在消息末尾写结果；/subs 列表里的（第二个参数 l）处理后重新列出。"""
        key, origin = (args + ["", ""])[:2]
        item = manage.find_suggestion(self.service, key)
        if not item:
            return None, None, "这条建议已经处理过了"
        name = f"《{messages.esc(item['title'])}》第 {item['season']} 季"
        buttons = None
        try:
            if action == "ss":
                result = await self.service.add_subscription(
                    {"title": item["title"], "season": item["season"], "tmdbid": item["tmdbid"]})
                done = f"🔔 <b>已订阅</b>{name}，之后的新集自动转存"
                if result["pending"]:
                    done += f"；待审批里还有 {len(result['pending'])} 条属于这部剧"
                    buttons = messages.PENDING_BUTTONS
            else:
                self.service.dismiss_suggestion(item)
                done = f"🙅 不再建议订阅{name}，需要时仍可手动订阅"
        except (ConflictError, NotFoundError, ValueError) as exc:
            return None, None, str(exc)[:190]
        if origin == "l":
            text, rows = manage.subs_list(self.service)
            return f"{done}\n\n{text}", rows, None
        return (messages.with_result(original, done) if original else done), buttons, None

    def _subscribed(self, ctx) -> bool:
        return subscriptions.subscribed(self.service.store.subscriptions(), ctx)

    def _approval_buttons(self, ctx) -> list:
        return messages.approval_buttons(ctx, subscribed=self._subscribed(ctx))

    def _result_buttons(self, ctx) -> list:
        return messages.result_buttons(ctx, subscribed=self._subscribed(ctx))

    def _pending(self, task_id: str) -> bool:
        ctx = self.service.store.get_task(task_id)
        return bool(ctx and ctx.state == TaskState.PENDING_APPROVAL)

    async def _subscribe(self, ctx, card: str, links: list):
        """追剧：订阅这部剧这一季（已订阅的跳过）；还在待审批时这一集一并转存。"""
        task_id = ctx.task_id
        if ctx.state in (TaskState.REJECTED, TaskState.SKIPPED):
            return messages.approval_result(card, "stale", ctx.state.value), links, None
        lines, toast = [], []
        if not self._subscribed(ctx):
            try:
                result = await self.service.subscribe_from_task(task_id)
            except (ConflictError, NotFoundError, ValueError) as exc:
                return None, None, str(exc)[:190]
            item, others = result["subscription"], len([tid for tid in result["pending"] if tid != task_id])
            lines.append(f"🔔 已订阅《{messages.esc(item['title'])}》第 {item['season']} 季，之后的新集自动转存"
                         + (f"；待审批里还有 {others} 条这部剧，可以用 /pending 处理" if others else ""))
            toast.append("已订阅")
        if ctx.state == TaskState.PENDING_APPROVAL:
            try:
                await self.service.approve(task_id)
            except (ConflictError, NotFoundError):
                current = self.service.store.get_task(task_id)
                lines.append(messages.approval_outcome("stale", current.state.value if current else None))
            else:
                lines.append(messages.approval_outcome("approved"))
                toast.append("开始转存")
        current = self.service.store.get_task(task_id) or ctx
        return messages.with_result(card, "\n".join(lines)), self._result_buttons(current), "，".join(toast) or None

    async def _ignore_work(self, task_id: str, card: str, links: list):
        """不再接收：记下这部作品，并忽略待审批里同一部作品的任务（含这条）。"""
        try:
            result = await self.service.ignore_work(task_id)
        except (ConflictError, NotFoundError, ValueError) as exc:
            return messages.with_result(card, f"ℹ️ {messages.esc(exc)}"), links, None
        others = len([tid for tid in result["rejected"] if tid != task_id])
        text = (f"⛔ <b>已忽略</b>，之后《{messages.esc(result['work']['title'])}》的消息不再处理"
                + (f"；待审批里另有 {others} 条一并忽略" if others else ""))
        return messages.with_result(card, text), links, None

    async def decide(self, data: str, card: str | None = None) -> str:
        """审批：``ok`` 转存、``to:<task>:<目录别名的短键>`` 存到某个保存目录、``no`` 忽略。

        返回新的卡片内容：card（卡片原文，省略时按任务重新生成）末尾加一行结果。
        """
        action, args = parse_callback(data)
        task_id, key = (args + ["", ""])[:2]
        ctx = self.service.store.get_task(task_id)
        card = card or (messages.approval_card(ctx) if ctx else f"<code>{messages.esc(task_id)}</code>")
        target = None
        if action == "to":
            target = next((alias for alias in self.service.pipeline.cfg["save_dirs"] if short_key(alias) == key), None)
            if target is None:  # 目录改名或删掉了
                return messages.approval_result(card, "stale")
        try:
            if action == "no":
                await self.service.reject(task_id)
            else:
                await self.service.approve(task_id, save_dir_alias=target)
        except (ConflictError, NotFoundError):
            current = self.service.store.get_task(task_id)
            return messages.approval_result(card, "stale", current.state.value if current else None)
        return messages.approval_result(card, "rejected" if action == "no" else "approved", target=target)

    def _is_owner(self, event) -> bool:
        return str(getattr(event, "sender_id", "")) == str(self._owner)

    async def chat(self, text: str, *, chat: str = "owner") -> tuple[str, list[tuple[str, str]] | None]:
        """对话：返回 (回复文字, 按钮)；有待确认的写操作时附「执行 / 取消」按钮。"""
        try:
            reply = await self.assistant.handle(text, chat=chat)
        except Exception as exc:  # noqa: BLE001 - 对话失败不影响命令与审批按钮
            log.warning("对话助手出错：%s", exc)
            return f"出错了（{type(exc).__name__}），请稍后再试或使用 /help 里的命令。", None
        if not reply.plan_id:
            return reply.text, None
        return reply.text, [("✅ 执行", messages.callback("do", reply.plan_id)),
                            ("取消", messages.callback("cancel", reply.plan_id))]

    async def confirm(self, data: str) -> str:
        action, args = parse_callback(data)
        plan_id = args[0] if args else ""
        if action == "cancel":
            self.assistant.cancel(plan_id)
            return "已取消，没有执行任何操作。"
        return await self.assistant.execute(plan_id)

    def _buttons(self, buttons):
        """按钮：一行 [(文字, 数据)] 或多行 [[...], [...]]；数据以 https:// 开头的是链接按钮。"""
        if not buttons or not self._Button:
            return None
        rows = buttons if isinstance(buttons[0], list) else [buttons]
        make = lambda label, data: (self._Button.url(label, data) if data.startswith("https://")  # noqa: E731
                                    else self._Button.inline(label, data.encode()))
        return [[make(label, data) for label, data in row] for row in rows if row]

    async def send(self, text: str, buttons=None, chat=None, html: bool = False, photo: str | None = None,
                   card: str | None = None) -> None:
        """供 NotifyProvider 调用：把消息发给 owner（或指定 chat）。html=True 时按 HTML 解析（模板见 core/messages.py）；
        按钮见 _buttons。photo 是图片地址（如 TMDB 海报）：以图片发送、正文作为说明；说明超出 Telegram 的
        长度限制或图片发送失败时退回纯文字。card 是审批卡片对应的任务：记下这条消息，任务在别处处理后改成结果。"""
        if not self._client:
            log.info("[控制bot未启用] %s", (messages.plain(text) if html else text).replace("\n", " / "))
            return
        target = chat or int(self._owner)
        sent = await self._deliver(target, text, buttons, "html" if html else (), photo)
        if card and sent is not None:
            self.service.store.card_add(card, str(target), sent.id)

    async def _deliver(self, target, text: str, buttons, mode, photo: str | None):
        if photo and len(messages.plain(text) if mode else text) <= CAPTION_LIMIT:
            try:
                return await self._client.send_file(target, photo, caption=text, buttons=self._buttons(buttons),
                                                    parse_mode=mode)
            except Exception as exc:  # noqa: BLE001 - 海报只是锦上添花，发不出就发文字
                log.warning("海报发送失败，改发文字：%s", exc)
        return await self._client.send_message(target, text, buttons=self._buttons(buttons), parse_mode=mode)

    async def sync_cards(self) -> int:
        """把在别处处理过的审批卡片改成处理结果、去掉审批按钮，返回改了几张。

        管理台、对话助手、批量操作，以及同一部作品点了「不再接收」时，任务离开待审批，卡片却还停在原样。
        卡片被删了、改不了时不再重试；连不上 Telegram 时抛出，下次再改。
        """
        from telethon.errors import MessageNotModifiedError, RPCError
        store, changed = self.service.store, 0
        async with self._card_lock:
            for row in store.cards_settled():
                ctx = store.get_task(row["task_id"])
                if ctx is not None:
                    chat = row["chat"]
                    work = ignored.match(store.ignored_works(), ctx.parsed, ctx.meta.get("tmdb", {}))
                    try:
                        await self._client.edit_message(
                            int(chat) if chat.lstrip("-").isdigit() else chat, row["message_id"],
                            messages.settled_card(ctx, ignored=work is not None), parse_mode="html",
                            buttons=self._buttons(self._result_buttons(ctx)))
                        changed += 1
                    except MessageNotModifiedError:
                        pass
                    except RPCError as exc:  # 卡片已被删除等
                        log.info("审批卡片没能更新（任务 %s）：%s", row["task_id"], exc)
                store.card_drop(row["chat"], row["message_id"])
        return changed

    async def _sync_loop(self) -> None:
        reported = None
        while True:
            await asyncio.sleep(CARD_SYNC_SEC)
            try:
                await self.sync_cards()
                reported = None
            except Exception as exc:  # noqa: BLE001 - 连不上 Telegram 等：下次再改
                if str(exc) != reported:
                    log.warning("同步审批卡片失败：%s", exc)
                reported = str(exc)

    async def run_forever(self) -> None:
        """保持控制 bot 长驻（在 gather 里与监听/worker 并发），同时同步审批卡片。

        与 Telegram 断开（连接被重置、心跳没有回应、处理更新出错）后在进程里重新连接，不让进程退出。
        """
        if not self._client:
            return
        sync = asyncio.create_task(self._sync_loop())
        try:
            while True:
                if self._client.is_connected():
                    try:
                        await self._client.run_until_disconnected()
                        reason = "连接已断开"
                    except Exception as exc:  # noqa: BLE001
                        reason = f"{type(exc).__name__}：{telegram.describe(exc)}"
                    log.info("控制 bot 与 Telegram 断开（%s），%s 秒后重新连接", reason, telegram.RECONNECT_SEC)
                await self._reconnect()
        finally:
            sync.cancel()

    async def _reconnect(self) -> None:
        """用新的客户端重新连接，连不上时每 RETRY_SECONDS 秒再试；Bot Token 失效等不是网络的问题照常抛出。"""
        delay = telegram.RECONNECT_SEC
        while True:
            await asyncio.sleep(delay)
            await self._client.disconnect()
            try:
                await self._open()
            except Exception as exc:
                if not telegram.transient(exc):
                    raise
                if telegram.describe(exc) != self._problem:
                    log.warning("控制 bot 连不上 Telegram：%s，每 %s 秒重试一次", telegram.describe(exc), RETRY_SECONDS)
                self._problem, delay = telegram.describe(exc), RETRY_SECONDS
            else:
                log.info("控制 bot 已重新连上")
                return

    def health(self) -> dict:
        if not self.enabled:  # 没配置不算断开：总览显示「未配置」而不是连接中断
            return {"status": "not_configured",
                    "error": "未配置：在「设置 → Telegram」填写控制 Bot Token 与你的数字 ID，重启主进程后生效"}
        if self._client and self._client.is_connected():
            return {"status": "connected"}
        return {"status": "disconnected", **({"error": f"连不上 Telegram：{self._problem}"} if self._problem else {})}

    async def close(self) -> None:
        if self._client:
            await self._client.disconnect()
