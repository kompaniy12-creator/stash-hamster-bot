"""Один прогон хомяка в GitHub Actions.

Забирает новые сообщения из Telegram, обрабатывает их теми же хендлерами, что и локальный бот,
дожидается разборов, принимает отчёты от mac_helper и при необходимости напоминает.
Состояние — data/norka.json (workflow расшифровывает его до прогона и шифрует после).
"""
import asyncio
import datetime as dt
import hashlib
import json
import re
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("STASH_MODE", "cloud")
# Очередь Cloudflare Worker (мгновенный режим). Читаем до импорта bot — он чистит секреты из окружения.
QUEUE_URL = os.getenv("STASH_QUEUE_URL", "").rstrip("/")
QUEUE_SECRET = os.getenv("STASH_QUEUE_SECRET", "")
os.environ.pop("STASH_QUEUE_SECRET", None)

import httpx  # noqa: E402

from telegram import Update  # noqa: E402

import crypto_store  # noqa: E402  (до bot: ключ читается при импорте, bot потом чистит окружение)
import bot  # noqa: E402
import persona as P  # noqa: E402
import storage  # noqa: E402

RESULTS_DIR = Path(os.getenv("STASH_RESULTS", "norka/results"))
COMMANDS_VERSION = 6  # поднять, если поменялся список команд или кнопка меню


def ingest_results(tg_bot) -> int:
    """Отчёты об установке, которые mac_helper положил в results/."""
    n = 0
    for f in sorted(RESULTS_DIR.glob("*.json.enc")) if RESULTS_DIR.exists() else []:
        try:
            r = json.loads(crypto_store.decrypt_bytes(f.read_bytes()))
            if r.get("kind") in ("removal", "update"):
                qname = "remove_queue" if r["kind"] == "removal" else "update_queue"
                q = [x for x in (storage.meta_get(qname) or [])
                     if not (x["target"] == r["target"] and x["requested_at"] == r["requested_at"])]
                storage.meta_set(qname, q)
                f.unlink()
                n += 1
                continue
            item = storage.get(r["id"])
            if item and item.get("approved_at") == r.get("approved_at"):
                storage.update(r["id"], status=r["status"], log=r.get("report"))
        except Exception as e:  # noqa: BLE001
            print(f"bad result {f}: {e}", file=sys.stderr)
        f.unlink()
        n += 1
    return n


async def reminders(tg_bot) -> None:
    now = dt.datetime.now()
    today = now.date().isoformat()
    ctx = SimpleNamespace(bot=tg_bot)
    digest_day = (int(os.getenv("REMINDER_WEEKDAY", "1")) - 1) % 7  # 1=пн в терминах Telegram-бота
    if (now.weekday() == digest_day and now.hour >= int(os.getenv("REMINDER_HOUR", "10"))
            and storage.meta_get("last_digest") != today):
        storage.meta_set("last_digest", today)
        await bot.weekly_reminder(ctx)
    if now.hour >= int(os.getenv("NUDGE_HOUR", "19")) and storage.meta_get("last_nudge") != today:
        storage.meta_set("last_nudge", today)
        await bot.daily_nudge(ctx)


async def fetch_updates(tg_bot) -> tuple[list[Update], list[int]]:
    """Апдейты из очереди Worker (webhook) или напрямую из Telegram (getUpdates)."""
    if QUEUE_URL:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{QUEUE_URL}/drain", headers={"authorization": f"Bearer {QUEUE_SECRET}"})
            r.raise_for_status()
        rows = r.json()
        ups = []
        for row in rows:
            u = Update.de_json(json.loads(row["body"]), tg_bot)
            if row.get("ack_id"):
                bot.ACK[u.update_id] = int(row["ack_id"])
            ups.append(u)
        return ups, [row["id"] for row in rows]
    offset = storage.meta_get("tg_offset")
    return list(await tg_bot.get_updates(offset=offset, timeout=0, allowed_updates=Update.ALL_TYPES)), []


async def confirm(ids: list[int]) -> None:
    if QUEUE_URL and ids:
        async with httpx.AsyncClient(timeout=20) as c:
            await c.post(f"{QUEUE_URL}/done", json={"ids": ids},
                         headers={"authorization": f"Bearer {QUEUE_SECRET}"})


def catalog_snapshot() -> dict:
    items = []
    for it in storage.list_items(limit=1000):
        try:
            plan = json.loads(it.get("install_plan") or "[]")
        except ValueError:
            plan = [it["install_plan"]]
        names = it.get("install_names") or []
        if isinstance(names, str):
            names = json.loads(names or "[]")
        can = it.get("can") or []
        if isinstance(can, str):
            can = json.loads(can or "[]")
        title = it.get("title") or ""
        names = sorted({n.lower() for n in names} | {w.lower() for w in re.findall(r"[a-z0-9][a-z0-9_.-]+[a-z0-9]", title, re.I) if "-" in w})
        ov = it.get("overlaps") or []
        if isinstance(ov, str):
            ov = json.loads(ov or "[]")
        items.append({"overlaps": ov, "id": it["id"], "title": title, "kind": it.get("kind"), "summary": it.get("summary"),
                      "plan": plan, "risk": it.get("risk"), "status": it["status"], "comment": it.get("comment"),
                      "can": can, "names": names, "created_at": it.get("created_at"),
                      "report": (it.get("log") or "")[:4000] if it["status"] != "ready" else None})
    return {"items": items, "remove_queue": storage.meta_get("remove_queue") or [],
            "update_queue": storage.meta_get("update_queue") or []}


async def load_inventory() -> None:
    """Опись Mac из Worker — чтобы при разборе сравнивать новое с уже установленным."""
    if not QUEUE_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{QUEUE_URL}/snapshot/read", json={"name": "inventory"},
                             headers={"authorization": f"Bearer {QUEUE_SECRET}"})
        if r.status_code == 200:
            bot.INVENTORY = r.json()
    except httpx.HTTPError:
        pass


async def push_catalog() -> None:
    """Отдать каталог дашборду (только если изменился)."""
    if not QUEUE_URL:
        return
    body = json.dumps(catalog_snapshot(), ensure_ascii=False, sort_keys=True)
    sha = hashlib.sha256(body.encode()).hexdigest()
    if storage.meta_get("dash_sha") == sha:
        return
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{QUEUE_URL}/snapshot/catalog", content=body.encode(),
                         headers={"authorization": f"Bearer {QUEUE_SECRET}", "content-type": "application/json"})
    if r.status_code == 200:
        storage.meta_set("dash_sha", sha)


async def main() -> None:
    app = bot.build_app()
    await app.initialize()
    try:
        if storage.meta_get("commands_version") != COMMANDS_VERSION:
            await app.bot.set_my_commands(P.COMMANDS)
            if bot.dashboard_url():
                from telegram import MenuButtonWebApp, WebAppInfo
                await app.bot.set_chat_menu_button(chat_id=bot.OWNER_ID, menu_button=MenuButtonWebApp(
                    "🐹 Норка", WebAppInfo(bot.dashboard_url())))
            storage.meta_set("commands_version", COMMANDS_VERSION)

        got = ingest_results(app.bot)
        await load_inventory()
        updates, queue_ids = await fetch_updates(app.bot)
        seen = set(storage.meta_get("seen_updates") or [])
        for u in sorted(updates, key=lambda x: x.update_id):
            if u.update_id in seen:
                continue  # уже обработан в прошлом прогоне
            seen.add(u.update_id)  # сначала отмечаем: упавшее сообщение не зациклится
            storage.meta_set("seen_updates", sorted(seen, key=abs)[-500:])
            if u.update_id > 0:
                storage.meta_set("tg_offset", u.update_id + 1)
            await app.process_update(u)
        await confirm(queue_ids)
        while bot.PENDING:  # разборы, запущенные хендлерами
            batch, bot.PENDING[:] = bot.PENDING[:], []
            await asyncio.gather(*batch, return_exceptions=True)
        await reminders(app.bot)
        await push_catalog()
        print(f"updates={len(updates)} results={got}")
    finally:
        await app.shutdown()


if __name__ == "__main__":
    storage.init()
    asyncio.run(main())
