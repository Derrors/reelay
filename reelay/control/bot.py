"""控制 Bot —— 可观测与人工干预（评审 P0-03）。

用你自己的 Telegram Bot（管理台「设置」中填写 Bot Token）作为控制入口，提供：

- 命令：``/status``（队列积压、各状态计数、在线状态）、``/pending``（待审批与待人工，点开卡片处理，可一键全部批准
  或忽略）、``/failed``（失败任务）、``/subs``（追剧订阅，可取消或继续订阅）、``/ignored``（不再接收的作品，可恢复）、
  ``/retry <task_id>``、``/help``；列表与修正识别见 ``control/manage.py``，不依赖 AI
- 对话：不以 ``/`` 开头的文字交给对话助手（``control/assistant.py``），如「我有哪些 4K 日本动画」
  「把《X》这几集都批了，存到动漫」；写操作先回显要执行的动作，点「执行」后才执行
- 卡片按钮回调（callback_data 只带短 id，评审 P0-01），见 ``callback``：审批 ``ok`` / ``no`` / ``dir``（换目录）/
  ``to``（存到某个目录）/ ``sub``（追剧）/ ``blk`` → ``bly``（不再接收，先确认再执行）/ ``back``；需要处理的任务 ``rc``（核验转存）/ ``ro``（重新整理）/ ``rt``（重试）/
  ``ap``（按已到的文件继续）/ ``cf`` → ``cy``（删除转存文件、覆盖保存、放弃并删除，先确认再执行）/ ``pb``；
  修正识别 ``fx``（发出提示，回复它写要改的项）/ ``rp``（重新识别）/ ``fxn``；列表 ``cd``（重新发出卡片）/ ``pl`` ``fl``（翻页）/
  ``pa`` ``pr`` → ``pay`` ``pry``（全部批准、全部忽略）/ ``us`` → ``usy``（取消订阅）/ ``so``（继续订阅）/ ``ui``（恢复接收）；
  订阅建议 ``ss``（订阅）/ ``sd``（不用了）；追更提醒 ``tm``（某部剧的选项）/ ``tmi``（现在缺的集不用等了）/ ``tmo``（不再提醒这部）/ ``tmu``（撤销）；
  对话助手的确认按钮 ``do`` / ``cancel``
- 对外暴露 ``send(...)``：给 NotifyProvider 用来推确认卡片（带按钮）与结果通知

未配置 token（dry-run / 未接入）时，本模块不启动，通知走 ConsoleNotify 打印。
消息只发给 ``telegram.owner_id``（你自己的数字 id），不广播。
"""

from __future__ import annotations

import logging
import re
import time

from ..core import messages, paths, runtime, secret_store, subscriptions
from ..core.contracts import TaskState
from ..core.service import ConflictError, ManagementService, NotFoundError
from ..plugins import episode_tracker
from . import manage
from .assistant import Assistant

log = logging.getLogger("reelay.control.bot")

# 图片说明的长度上限是 1024；留出余量给审批后在末尾追加的结果行
CAPTION_LIMIT = 960

CORRECTION_TTL = 600  # 点「修正」后多久内发的消息当作修正内容
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

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._owner)

    async def start(self) -> None:
        if not self.enabled:
            log.info("控制 bot 未配置（缺 token/owner），跳过；通知走控制台")
            return
        from telethon import TelegramClient, events, Button  # 延迟导入
        self._Button = Button

        api_id = int(secret_store.get("telegram_api_id") or 0)
        api_hash = secret_store.get("telegram_api_hash") or ""
        self._client = TelegramClient(str(paths.CONTROL_BOT_SESSION), api_id, api_hash)

        self._register_handlers(events)
        await self._client.start(bot_token=self._token)
        log.info("控制 bot 已上线")
        try:
            from telethon import functions, types
            await self._client(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(), lang_code="",
                commands=[types.BotCommand(command=name, description=text) for name, text in manage.COMMAND_MENU]))
        except Exception as exc:  # noqa: BLE001 - 命令菜单只是方便输入
            log.warning("设置控制 bot 命令菜单失败：%s", exc)

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
                await self.send(text, buttons, chat=e.chat_id, html=True, photo=photo)
                return
            text, buttons = await self.chat(e.raw_text, chat=str(e.chat_id))
            await e.reply(text, buttons=self._buttons(buttons))

        @c.on(events.CallbackQuery())
        async def _cb(e):
            if str(e.sender_id) != str(self._owner):
                return
            data = (e.data or b"").decode()
            if data.startswith(("do:", "cancel:")):
                await e.edit(await self.confirm(data))
                return
            sent = await self.open(data, chat=str(e.chat_id))
            if sent is not None:  # 发一条新消息（卡片、修正提示），原消息不动
                text, buttons, photo, toast = sent
                if text:
                    await self.send(text, buttons, chat=e.chat_id, html=True, photo=photo)
                await e.answer(toast or "")
                return
            from telethon.extensions import html as tl_html
            message = await e.get_message()
            original = tl_html.unparse(message.message or "", message.entities or []) if message else ""
            text, buttons, toast = await self.callback(data, original)
            from telethon.errors import MessageNotModifiedError
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
            await e.answer(toast or "")

    async def callback(self, data: str, original: str = "") -> tuple[str | None, list | None, str | None]:
        """卡片按钮：返回 (新正文，None 表示不改；新按钮；顶部提示)。original 是卡片当前的正文（HTML）。

        执行后正文原样保留、末尾加一行结果，按钮只留链接（如 TMDB 详情）。
        """
        action, _, rest = data.partition(":")
        listed = await self.manage(action, rest, original)
        if listed is not None:
            return listed
        task_id, _, arg = rest.partition(":")
        ctx = self.service.store.get_task(task_id)
        links = messages.link_buttons(ctx) if ctx else []
        card = original or (messages.approval_card(ctx) if ctx else f"<code>{messages.esc(task_id)}</code>")
        pending = bool(ctx and ctx.state == TaskState.PENDING_APPROVAL)
        # 「忽略」也出现在失败任务的提醒上：那时是不再处理（见下方 operations）
        if action in ("ok", "to", "dir", "back", "sub", "blk", "bly") or (action == "no" and pending):
            if not pending:
                return messages.approval_result(card, "stale", ctx.state.value if ctx else None), links, None
            if action == "dir":
                return None, messages.directory_buttons(task_id, list(self.service.pipeline.cfg["save_dirs"])), None
            if action == "back":
                return None, self._approval_buttons(ctx), None
            if action == "blk":
                return None, messages.block_confirm_buttons(ctx), "确认后，这部作品之后的消息都会直接忽略；可以用 /ignored 恢复"
            if action == "sub":
                return await self._subscribe(task_id, card)
            if action == "bly":
                return await self._ignore_work(task_id, card, links)
            return await self.decide(data, card), links, None
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
        if action not in operations or (action == "cy" and arg not in messages.CONFLICT_CHOICES):
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
        action, _, task_id = data.partition(":")
        if action not in ("cd", "fx"):
            return None
        ctx = self.service.store.get_task(task_id)
        if not ctx:
            return None, None, None, "任务不存在"
        if action == "cd":
            text, buttons, photo = messages.task_card(
                ctx, subscribed=subscriptions.subscribed(self.service.pipeline.cfg, ctx))
            return text, buttons, photo, ""
        if not messages.correctable(ctx):
            return None, None, None, f"现在不能修正（当前：{ctx.state.value}）"
        self._correcting[chat] = (task_id, time.time() + CORRECTION_TTL)
        return messages.correction_prompt(ctx), messages.correction_buttons(task_id), None, "发一句要改的内容"

    def correction_target(self, chat: str, replied: str | None) -> str | None:
        """这条消息是不是修正内容：回复了修正提示，或点「修正」后十分钟内发的第一条消息。"""
        match = _CORRECTION_RE.search(replied or "")
        if match:
            return match.group(1)
        task_id, expires = self._correcting.pop(chat, (None, 0))
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
        text, buttons, photo = messages.task_card(ctx, subscribed=subscriptions.subscribed(self.service.pipeline.cfg, ctx))
        changes = messages.esc(manage.describe_changes(before, ctx.parsed))
        return messages.with_result(text, f"✏️ <b>已修正</b>：{changes}"), buttons, photo

    async def manage(self, action: str, rest: str, original: str = "") -> tuple[str | None, list | None, str | None] | None:
        """列表与追更提醒的按钮（不绑定单条任务）；不是这些按钮时返回 None。"""
        service = self.service
        if action in ("pl", "fl"):
            offset = int(rest) if rest.isdigit() else 0
            text, buttons = (manage.pending_list if action == "pl" else manage.failed_list)(service, offset)
            return text, buttons, None
        if action in ("pa", "pr"):
            text, buttons = manage.bulk_confirm(service, action)
            return text, buttons, None
        if action in ("pay", "pry"):
            return await manage.bulk_run(service, action), [[("📋 查看待处理", "pl:0")]], None
        if action in ("us", "usy", "so"):
            item = manage.find_subscription(service, rest)
            if not item:
                return None, None, "这条订阅已经不在了"
            name = f"《{messages.esc(item['title'])}》第 {item['season']} 季"
            if action == "us":
                return (f"⚠️ 取消订阅{name}？之后的新集不再自动转存，也不再巡检缺集。",
                        [[("确认取消订阅", f"usy:{rest}"), ("↩ 返回", "sl")]], None)
            try:
                if action == "usy":
                    await service.remove_subscription(item)
                else:
                    service.reopen_subscription(item)
            except (ConflictError, NotFoundError, ValueError) as exc:
                return None, None, str(exc)[:190]
            text, buttons = manage.subs_list(service)
            done = f"🗑 已取消订阅{name}" if action == "usy" else f"▶️ 已继续订阅{name}，新集照常自动转存"
            return f"{done}\n\n{text}", buttons, None
        if action in ("ss", "sd"):
            return await self._suggestion(action, rest, original)
        if action in ("sl", "st"):
            text, buttons = (manage.subs_list if action == "sl" else manage.status_view)(service)
            return text, buttons, None
        if action == "ui":
            item = manage.find_ignored(service, rest)
            if not item:
                return None, None, "这条记录已经不在了"
            try:
                await service.unignore_work(item)
            except (ConflictError, NotFoundError, ValueError) as exc:
                return None, None, str(exc)[:190]
            text, buttons = manage.ignored_list(service)
            return f"↩️ 已恢复接收《{messages.esc(item['title'])}》\n\n{text}", buttons, None
        if action in ("tm", "tmi", "tmo", "tmu", "tmb"):
            return self._tracker(action, rest, original)
        return None

    async def _suggestion(self, action: str, rest: str, original: str) -> tuple[str | None, list | None, str | None]:
        """订阅建议的「订阅」「不用了」：推送来的建议在消息末尾写结果；/subs 列表里的（:l）处理后重新列出。"""
        key, _, origin = rest.partition(":")
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

    def _tracker(self, action: str, digest: str, original: str = "") -> tuple[str | None, list | None, str | None]:
        """追更提醒上某部剧的按钮：选项只换按钮，执行后在提醒末尾加一行结果。"""
        state = runtime.PluginState(self.service.store, episode_tracker.EpisodeTracker.name)
        found = episode_tracker.find_series(state, digest)
        if not found:
            return None, None, "这条提醒已经过期，等明天的巡检"
        key, info = found
        title, _, season = key.rpartition("|")
        if not info and action not in ("tmu", "tmb"):
            return None, None, "这部剧已经不再提醒"
        if action == "tmb":
            return None, episode_tracker.buttons(state.get("last_report", {}).get("series") or {}), None
        if action == "tm":
            missing = episode_tracker.format_episodes(info["missing"], limit=3)
            return None, [[(f"🙈 {missing} 不用等了"[:40], f"tmi:{digest}")],
                          [(f"🔕 不再提醒 {title}"[:40], f"tmo:{digest}"), ("↩ 返回", f"tmb:{digest}")]], None
        if action == "tmu":
            episode_tracker.unmute(state, key)
            text = messages.with_result(original, f"↩ 《{messages.esc(title)}》{season} 照常提醒") if original else None
            return text, episode_tracker.buttons(state.get("last_report", {}).get("series") or {}), "已撤销"
        if action == "tmi":
            episode_tracker.mute(state, key, info["missing"])
            done = f"🙈 《{title}》{season} 现在缺的 {episode_tracker.format_episodes(info['missing'])} 不再提醒，之后新缺的集照常提醒"
        else:
            episode_tracker.mute(state, key)
            done = f"🔕 《{title}》{season} 不再提醒缺集（订阅的自动转存不受影响）"
        undo = [[("↩ 撤销", f"tmu:{digest}")]]
        return (messages.with_result(original, messages.esc(done)) if original else None), undo, done[:190]

    def _approval_buttons(self, ctx) -> list:
        return messages.approval_buttons(ctx, subscribed=subscriptions.subscribed(self.service.pipeline.cfg, ctx))

    async def _subscribe(self, task_id: str, card: str):
        """追剧：订阅这部剧这一季；这条仍等审批，按钮保留（去掉「追剧」）。"""
        try:
            result = await self.service.subscribe_from_task(task_id)
        except (ConflictError, NotFoundError, ValueError) as exc:
            return None, None, str(exc)[:190]
        ctx = self.service.store.get_task(task_id)
        item = result["subscription"]
        text = messages.with_result(card, f"🔔 已订阅《{messages.esc(item['title'])}》第 {item['season']} 季，"
                                          "之后的新集自动转存；这一集仍等你确认")
        return text, messages.approval_buttons(ctx, subscribed=True), "已订阅"

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
        """审批：``ok`` 转存、``to:<task>:<序号>`` 存到某个保存目录、``no`` 忽略。

        返回新的卡片内容：card（卡片原文，省略时按任务重新生成）末尾加一行结果。
        """
        action, _, rest = data.partition(":")
        task_id, _, index = rest.partition(":")
        ctx = self.service.store.get_task(task_id)
        card = card or (messages.approval_card(ctx) if ctx else f"<code>{messages.esc(task_id)}</code>")
        target = None
        if action == "to":
            aliases = list(self.service.pipeline.cfg["save_dirs"])
            if not index.isdigit() or int(index) >= len(aliases):
                return messages.approval_result(card, "stale")
            target = aliases[int(index)]
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
        return reply.text, [("✅ 执行", f"do:{reply.plan_id}"), ("取消", f"cancel:{reply.plan_id}")]

    async def confirm(self, data: str) -> str:
        action, _, plan_id = data.partition(":")
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

    async def send(self, text: str, buttons=None, chat=None, html: bool = False, photo: str | None = None) -> None:
        """供 NotifyProvider 调用：把消息发给 owner（或指定 chat）。html=True 时按 HTML 解析（模板见 core/messages.py）；
        按钮见 _buttons。photo 是图片地址（如 TMDB 海报）：以图片发送、正文作为说明；说明超出 Telegram 的
        长度限制或图片发送失败时退回纯文字。"""
        if not self._client:
            log.info("[控制bot未启用] %s", (messages.plain(text) if html else text).replace("\n", " / "))
            return
        target, mode = chat or int(self._owner), "html" if html else ()
        if photo and len(messages.plain(text) if html else text) <= CAPTION_LIMIT:
            try:
                await self._client.send_file(target, photo, caption=text, buttons=self._buttons(buttons), parse_mode=mode)
                return
            except Exception as exc:  # noqa: BLE001 - 海报只是锦上添花，发不出就发文字
                log.warning("海报发送失败，改发文字：%s", exc)
        await self._client.send_message(target, text, buttons=self._buttons(buttons), parse_mode=mode)

    async def run_forever(self) -> None:
        """保持控制 bot 长驻（在 gather 里与监听/worker 并发）。"""
        if self._client:
            await self._client.run_until_disconnected()

    def health(self) -> dict:
        return {"status": "connected" if self._client and self._client.is_connected() else "disconnected"}

    async def close(self) -> None:
        if self._client:
            await self._client.disconnect()
