"""Стэш — хомяк-кладовщик в Telegram: собирает skills и сервисы, ставит, напоминает."""
import asyncio
import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv(Path(__file__).parent / ".env")

import tgfmt  # noqa: E402
from progress import Progress  # noqa: E402
import agent  # noqa: E402  (после load_dotenv, чтобы подхватить AGENT_MODEL)
import persona as P  # noqa: E402
import storage  # noqa: E402

# В облаке логи GitHub Actions публичные — пишем только самое необходимое, без содержимого запасов.
_CLOUD = os.getenv("STASH_MODE", "local") == "cloud"
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.ERROR if _CLOUD else logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("skill-inbox")


def log_exc(what: str, e: BaseException) -> None:
    if _CLOUD:
        log.error("%s: %s", what, type(e).__name__)  # без текста ошибки и трейсбэка
    else:
        log.exception(what)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_owner = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
if not TOKEN or not _owner.isdigit():
    raise SystemExit(
        "Заполни .env: TELEGRAM_BOT_TOKEN (от @BotFather) и "
        "TELEGRAM_ALLOWED_USER_ID (число от @userinfobot), сохрани файл и запусти снова."
    )
OWNER_ID = int(_owner)
agent.scrub_env()
# local — бот живёт на Mac и ставит сам; cloud — разовый прогон в GitHub Actions, установку делает mac_helper
MODE = os.getenv("STASH_MODE", "local")
PENDING: list[asyncio.Future] = []
ACK: dict[int, int] = {}  # update_id -> id сообщения «Хап!», которое уже отправил Cloudflare Worker


def spawn(ctx: ContextTypes.DEFAULT_TYPE, coro) -> None:
    """Фоновая задача: локально — в фоне бота, в облаке — копим, прогон дождётся всех."""
    if MODE == "cloud":
        PENDING.append(asyncio.ensure_future(coro))
    else:
        ctx.application.create_task(coro)
FILES_DIR = Path(__file__).parent / "data" / "files"
OWNER = filters.User(user_id=OWNER_ID)
MAX_LEN = 3900


def clip(s: str, n: int = MAX_LEN) -> str:
    return s if len(s) <= n else s[: n - 20] + "\n…(обрезано)"


def _risk_icon(risk: str) -> str:
    r = (risk or "").lower()
    return "🔴" if r.startswith("high") else "🟡" if r.startswith("medium") else "🟢" if r.startswith("low") else "⚪️"


def card(item: dict) -> str:
    """Карточка запаса в markdown (отправляется через tgfmt.send_md)."""
    one = lambda x: " ".join(str(x).split())  # noqa: E731
    lines = [f"**#{item['id']} · {one(item.get('title') or 'без названия')}**"]
    meta = [x for x in (item.get("kind"), storage.STATUSES.get(item["status"], item["status"])) if x]
    lines.append(" · ".join(meta))
    if item.get("summary"):
        lines += ["", one(item["summary"])]
    can = item.get("can")
    if isinstance(can, str):
        can = json.loads(can or "[]")
    if can:
        lines += ["", "**Что умеет**"] + [f"- {one(c)}" for c in can]
    ov = item.get("overlaps")
    if isinstance(ov, str):
        ov = json.loads(ov or "[]")
    if ov:
        label = {"duplicate": "🟰 то же самое", "replace": "🔁 можно заменить", "similar": "≈ похоже"}
        lines += ["", "**⚠️ Уже есть в норке**"] + [
            f"- {label.get(o.get('relation'), '≈')} `{o.get('name') or o['target']}` — {one(o.get('note', ''))}" for o in ov]
    if item.get("install_plan"):
        try:
            plan = json.loads(item["install_plan"])
        except json.JSONDecodeError:
            plan = [item["install_plan"]]
        lines += ["", "**План установки**"] + [f"{i}. {one(step)}" for i, step in enumerate(plan, 1)]
    if item.get("risk"):
        lines += ["", f"**Риск** {_risk_icon(item['risk'])} {one(item['risk'])}"]
    tail = [x for x in (item.get("comment"), P.card_footer(item["status"])) if x]
    if tail:
        lines += ["", "🐹 " + " ".join(tail).removeprefix("🐹 ")]
    return "\n".join(lines)


def replace_targets(item: dict) -> list[str]:
    ov = item.get("overlaps")
    if isinstance(ov, str):
        ov = json.loads(ov or "[]")
    return [o["target"] for o in ov or [] if o.get("relation") in ("replace", "duplicate")]


def buttons(item: dict) -> InlineKeyboardMarkup | None:
    i = item["id"]
    if item["status"] in ("ready", "manual", "failed") and replace_targets(item):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔁 Хот-своп: вместо старого", callback_data=f"replace:{i}")],
            [InlineKeyboardButton("🐹 Рядом со старым", callback_data=f"install:{i}"),
             InlineKeyboardButton(P.BTN_LATER, callback_data=f"later:{i}"),
             InlineKeyboardButton(P.BTN_SKIP, callback_data=f"skip:{i}")],
        ])
    if item["status"] in ("ready", "manual", "failed"):
        label = P.BTN_INSTALL if item["status"] != "failed" else P.BTN_RETRY
        return InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(label, callback_data=f"install:{i}"),
                InlineKeyboardButton(P.BTN_LATER, callback_data=f"later:{i}"),
                InlineKeyboardButton(P.BTN_SKIP, callback_data=f"skip:{i}"),
            ]]
        )
    return None


# ---------- фоновые задачи ----------

INVENTORY: dict | None = None  # опись Mac (облако подтягивает её из Worker перед прогоном)


def installed_listing() -> str | None:
    inv = INVENTORY
    if inv is None and MODE != "cloud":
        try:
            import inventory  # noqa: PLC0415
            inv = inventory.collect()
        except Exception:  # noqa: BLE001
            inv = None
    return agent.installed_listing(inv) if inv else None


async def run_analysis(item_id: int, chat_id: int, msg_id: int, ctx: ContextTypes.DEFAULT_TYPE):
    storage.update(item_id, status="analyzing")
    try:
        prog = await Progress(ctx.bot, chat_id, msg_id, f"🐹⌨️ Сканирую пакет #{item_id}…", expected=60).start()
        try:
            res = await agent.analyze(storage.get(item_id), installed=installed_listing(), on_tool=prog.event)
        finally:
            await prog.finish(ok=True, head=f"🐹 Скан #{item_id} завершён, собираю карточку…")
        status = "installed" if res.get("already_installed") else (
            "ready" if res.get("can_auto_install") else "manual"
        )
        storage.update(
            item_id,
            title=res.get("title"),
            kind=res.get("kind"),
            summary=res.get("summary"),
            install_plan=res.get("install_plan", []),
            can_auto_install=int(bool(res.get("can_auto_install"))),
            risk=res.get("risk"),
            comment=res.get("hamster_comment"),
            can=res.get("can") or [],
            overlaps=[o for o in (res.get("overlaps") or []) if isinstance(o, dict) and o.get("target")],
            install_names=res.get("install_names") or [],
            status=status,
        )
    except Exception as e:  # noqa: BLE001
        log_exc("analyze failed", e)
        storage.update(item_id, status="failed", log=str(e))
        await ctx.bot.edit_message_text(
            clip(P.analyze_failed(item_id, e)), chat_id, msg_id
        )
        return
    item = storage.get(item_id)
    await tgfmt.send_md(ctx.bot, chat_id, card(item), reply_markup=buttons(item), edit_message_id=msg_id)


async def run_install(item_id: int, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, replace: bool = False):
    targets = replace_targets(storage.get(item_id)) if replace else []
    if MODE == "cloud":
        storage.update(item_id, status="approved", approved_at=dt.datetime.now().isoformat(timespec="seconds"),
                       replace_targets=targets)
        await ctx.bot.send_message(chat_id, P.replace_queued(item_id, targets) if targets else P.approved(item_id))
        return
    storage.update(item_id, status="installing")
    title = storage.get(item_id).get("title") or "без названия"
    msg = await ctx.bot.send_message(chat_id, P.installing(item_id, title))
    prog = await Progress(ctx.bot, chat_id, msg.message_id, f"⌨️ Деплою #{item_id} «{title}»", expected=150).start()
    try:
        status, report = await agent.install(storage.get(item_id), on_tool=prog.event)
    except Exception as e:  # noqa: BLE001
        log_exc("install failed", e)
        status, report = "failed", str(e)
    await prog.finish(ok=status == "installed")
    if status == "installed" and targets:  # локальный режим: убираем старое сразу
        import remover  # noqa: PLC0415
        for t in targets:
            ok, msg = remover.remove(t)
            report += "\n\n" + P.removal_done(t.split(":", 1)[-1].split("@")[0], ok, msg)
    storage.update(item_id, status=status, log=report)
    item = storage.get(item_id)
    head = P.install_head(status)
    await tgfmt.send_md(ctx.bot, chat_id, f"**{head} · #{item_id} {item.get('title') or ''}**\n\n{report}",
                        edit_message_id=msg.message_id,
                        reply_markup=buttons(item))


# ---------- хендлеры ----------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(P.START)


async def on_material(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    text = m.text or m.caption or ""
    if m.forward_origin:
        text = f"[пересланное сообщение]\n{text}"
    # ссылки из entities (в т.ч. скрытые под текстом)
    urls = [e.url for e in (*(m.entities or ()), *(m.caption_entities or ())) if getattr(e, "url", None)]
    if urls:
        text += "\n\nСсылки: " + " ".join(urls)

    file_path = None
    if m.document:
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        f = await m.document.get_file()
        safe = re.sub(r"[^\w.\-]+", "_", Path(m.document.file_name or "file").name)[:100] or "file"
        dest = FILES_DIR / f"{m.message_id}_{safe}"
        await f.download_to_drive(dest)
        file_path = str(dest)

    if not text.strip() and not file_path:
        return
    item_id = storage.add(text.strip() or "(файл)", file_path)
    ack_id = ACK.get(update.update_id)
    reply = None
    if ack_id:  # Worker уже сказал «Хап!» — дописываем то же сообщение, а не шлём новое
        try:
            reply = await ctx.bot.edit_message_text(P.received(item_id), m.chat_id, ack_id)
        except Exception:  # noqa: BLE001
            reply = None
    if reply is None or reply is True:
        reply = await m.reply_text(P.received(item_id))
    spawn(ctx, run_analysis(item_id, reply.chat_id, reply.message_id, ctx))


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE, statuses=None):
    statuses = statuses if statuses is not None else ["ready", "manual", "failed", "new"]
    items = storage.list_items(statuses or None)
    if not items:
        await update.message.reply_text(P.empty_list())
        return
    lines = [
        f"#{it['id']} {storage.STATUSES.get(it['status'], it['status']).split(' ')[0]} "
        f"{it.get('title') or it['raw_input'][:50]} — {it.get('kind') or '?'}"
        for it in items
    ]
    head = P.list_head(len(items)) if statuses else f"💾 > cat ~/norka.log — записей: {len(items)}"
    await update.message.reply_text(clip(head + "\n\n" + "\n".join(lines) + "\n\n/show N — достать и показать"))


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_list(update, ctx, statuses=[])


async def cmd_norka(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    st = storage.stats()
    waiting = st.get("ready", 0) + st.get("manual", 0) + st.get("failed", 0)
    total = st.get("total", 0)
    text = (
        f"🐹⌨️ root@norka:~$ stats\n\nРанг: {P.rank(total)}\n\n"
        f"📦 Пакетов в логе: {total}\n"
        f"✅ В проде: {st.get('installed', 0)}\n"
        f"⏳ В очереди деплоя: {waiting}\n"
        f"🗑 В /dev/null: {st.get('skipped', 0)}"
    )
    if waiting:
        text += "\n\n" + ("Буфер не резиновый, давай деплоить → /list" if waiting > 3 else "Глянь /list, там пара пакетов.")
    await update.message.reply_text(text)


def _arg_id(ctx) -> int | None:
    try:
        return int(ctx.args[0].lstrip("#"))
    except (IndexError, ValueError):
        return None


async def cmd_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    i = _arg_id(ctx)
    item = storage.get(i) if i else None
    if not item:
        await update.message.reply_text("🐹 Missing argument. Синтаксис: /show 3")
        return
    await tgfmt.send_md(ctx.bot, update.effective_chat.id, card(item), reply_markup=buttons(item))


async def cmd_install(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    i = _arg_id(ctx)
    if not i or not storage.get(i):
        await update.message.reply_text("🐹 Missing argument. Синтаксис: /install 3")
        return
    spawn(ctx, run_install(i, update.effective_chat.id, ctx))


async def cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    i = _arg_id(ctx)
    if not i or not storage.get(i):
        await update.message.reply_text("🐹 Missing argument. Синтаксис: /skip 3")
        return
    storage.update(i, status="skipped")
    await update.message.reply_text(P.skipped() + f" (#{i})")


TARGET_RX = re.compile(r"^(skill|plugin|mcp):[A-Za-z0-9_.@+-]{1,120}$")


async def cmd_replace(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    i = _arg_id(ctx)
    if not i or not storage.get(i):
        await update.message.reply_text("🐹 Missing argument. Синтаксис: /replace 3")
        return
    spawn(ctx, run_install(i, update.effective_chat.id, ctx, replace=True))


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    targets = [t for t in (ctx.args or []) if TARGET_RX.match(t)][:80]
    if not targets:
        await update.message.reply_text("🐹 Что выпиливать? Синтаксис: /remove skill:имя (или plugin:имя@маркет, mcp:имя). "
                                        "Проще — через «🧹 Уборка» в норке.")
        return
    names = [t.split(":", 1)[1].split("@")[0] for t in targets]
    label = names[0] if len(names) == 1 else f"{len(names)} шт.: {', '.join(names[:5])}{'…' if len(names) > 5 else ''}"
    if MODE == "cloud":
        q = storage.meta_get("remove_queue") or []
        now = dt.datetime.now().isoformat(timespec="seconds")
        for t in targets:
            if not any(x["target"] == t for x in q):
                q.append({"target": t, "requested_at": now})
        storage.meta_set("remove_queue", q)
        await update.message.reply_text(P.removal_queued(label))
    else:
        import remover  # noqa: PLC0415
        for t, n in zip(targets, names):
            ok, msg = remover.remove(t)
            await update.message.reply_text(P.removal_done(n, ok, msg))


async def cmd_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = dashboard_url()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🧹 Открыть аудит", web_app=WebAppInfo(url + "#cleanup"))]]) if url else None
    await update.message.reply_text("🐹 Аудит стека — в норке: дубли, пересечения и всё, что висит без дела.",
                                    reply_markup=kb)


def dashboard_url() -> str | None:
    base = os.getenv("STASH_QUEUE_URL", "").rstrip("/")
    return f"{base}/app" if base else None


async def cmd_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = dashboard_url()
    if not url:
        await update.message.reply_text("🐹 Норка-приложение работает только в мгновенном режиме (Cloudflare Worker).")
        return
    await update.message.reply_text(
        "🐹⌨️ Открываю терминал норки: весь стек на Mac и всё, что в очереди.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💻 Войти в норку", web_app=WebAppInfo(url))]]),
    )


async def cmd_reanalyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    i = _arg_id(ctx)
    if not i or not storage.get(i):
        await update.message.reply_text("🐹 Missing argument. Синтаксис: /reanalyze 3")
        return
    reply = await update.message.reply_text(P.reanalyzing(i))
    spawn(ctx, run_analysis(i, reply.chat_id, reply.message_id, ctx))


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.answer("🐹 Access denied. Это не твоя норка.", show_alert=True)
        return
    action, raw_id = q.data.split(":")
    item_id = int(raw_id)
    try:
        await q.answer()  # в мгновенном режиме Worker уже ответил на кнопку
    except Exception:  # noqa: BLE001
        pass
    if action in ("install", "replace"):
        await q.edit_message_reply_markup(None)
        spawn(ctx, run_install(item_id, q.message.chat_id, ctx, replace=action == "replace"))
    elif action == "later":
        await q.edit_message_reply_markup(None)
        await q.message.reply_text(P.later(item_id))
    elif action == "skip":
        storage.update(item_id, status="skipped")
        await q.edit_message_reply_markup(None)
        await q.message.reply_text(P.skipped())


async def weekly_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    items = storage.list_items(["ready", "manual"])
    if not items:
        return
    lines = [f"#{it['id']} {it.get('title') or '?'} — {it.get('kind') or '?'}" for it in items]
    await ctx.bot.send_message(
        OWNER_ID,
        clip(P.digest_head(len(items)) + "\n\n" + "\n".join(lines) + "\n\n/show N или /install N"),
    )


async def daily_nudge(ctx: ContextTypes.DEFAULT_TYPE):
    """Раз в день хомяк достаёт из кладовки один забытый запас (не чаще раза в 3 дня на запас)."""
    if dt.date.today().weekday() == (int(os.getenv("REMINDER_WEEKDAY", "1")) - 1) % 7:
        return  # в день недельной ревизии не дублируем
    item = storage.nudge_candidate()
    if not item:
        return
    days = (dt.datetime.now() - dt.datetime.fromisoformat(item["created_at"])).days
    storage.update(item["id"], last_nudged=dt.datetime.now().isoformat(timespec="seconds"))
    await ctx.bot.send_message(
        OWNER_ID, P.nudge(item.get("title") or "без названия", item["id"], days), reply_markup=buttons(item)
    )


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log_exc("handler error", ctx.error)
    try:
        await ctx.bot.send_message(OWNER_ID, clip(P.error(ctx.error)))
    except Exception:  # noqa: BLE001
        pass


def build_app() -> Application:
    storage.init()

    async def post_init(a: Application):
        await a.bot.set_my_commands(P.COMMANDS)

    app = Application.builder().token(TOKEN).concurrent_updates(True).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start, filters=OWNER))
    app.add_handler(CommandHandler("list", cmd_list, filters=OWNER))
    app.add_handler(CommandHandler("all", cmd_all, filters=OWNER))
    app.add_handler(CommandHandler("norka", cmd_norka, filters=OWNER))
    app.add_handler(CommandHandler("show", cmd_show, filters=OWNER))
    app.add_handler(CommandHandler("install", cmd_install, filters=OWNER))
    app.add_handler(CommandHandler("reanalyze", cmd_reanalyze, filters=OWNER))
    app.add_handler(CommandHandler("skip", cmd_skip, filters=OWNER))
    app.add_handler(CommandHandler("app", cmd_app, filters=OWNER))
    app.add_handler(CommandHandler("replace", cmd_replace, filters=OWNER))
    app.add_handler(CommandHandler("remove", cmd_remove, filters=OWNER))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup, filters=OWNER))
    app.add_error_handler(on_error)
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(
        OWNER & ~filters.COMMAND & (filters.TEXT | filters.CAPTION | filters.Document.ALL),
        on_material,
    ))
    return app


def main():
    """Локальный режим: бот постоянно работает на Mac (launchd)."""
    app = build_app()
    tz = dt.datetime.now().astimezone().tzinfo
    app.job_queue.run_daily(
        weekly_reminder,
        time=dt.time(hour=int(os.getenv("REMINDER_HOUR", "10")), tzinfo=tz),
        days=(int(os.getenv("REMINDER_WEEKDAY", "1")),),
    )
    app.job_queue.run_daily(daily_nudge, time=dt.time(hour=int(os.getenv("NUDGE_HOUR", "19")), tzinfo=tz))
    log.info("Stash the hamster is awake 🐹")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
