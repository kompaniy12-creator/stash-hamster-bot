"""Один прогон хомяка в GitHub Actions.

Забирает новые сообщения из Telegram, обрабатывает их теми же хендлерами, что и локальный бот,
дожидается разборов, принимает отчёты от mac_helper и при необходимости напоминает.
Состояние — data/norka.json (workflow расшифровывает его до прогона и шифрует после).
"""
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("STASH_MODE", "cloud")

from telegram import Update  # noqa: E402

import bot  # noqa: E402
import crypto_store  # noqa: E402
import persona as P  # noqa: E402
import storage  # noqa: E402

RESULTS_DIR = Path(os.getenv("STASH_RESULTS", "norka/results"))
COMMANDS_VERSION = 2  # поднять, если поменялся список команд


def ingest_results(tg_bot) -> int:
    """Отчёты об установке, которые mac_helper положил в results/."""
    n = 0
    for f in sorted(RESULTS_DIR.glob("*.json.enc")) if RESULTS_DIR.exists() else []:
        try:
            r = json.loads(crypto_store.decrypt_bytes(f.read_bytes()))
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


async def main() -> None:
    app = bot.build_app()
    await app.initialize()
    try:
        if storage.meta_get("commands_version") != COMMANDS_VERSION:
            await app.bot.set_my_commands(P.COMMANDS)
            storage.meta_set("commands_version", COMMANDS_VERSION)

        got = ingest_results(app.bot)
        offset = storage.meta_get("tg_offset")
        updates = await app.bot.get_updates(offset=offset, timeout=0, allowed_updates=Update.ALL_TYPES)
        for u in updates:
            storage.meta_set("tg_offset", u.update_id + 1)  # сначала сдвигаем: упавшее сообщение не зациклится
            await app.process_update(u)
        while bot.PENDING:  # разборы, запущенные хендлерами
            batch, bot.PENDING[:] = bot.PENDING[:], []
            await asyncio.gather(*batch, return_exceptions=True)
        await reminders(app.bot)
        print(f"updates={len(updates)} results={got}")
    finally:
        await app.shutdown()


if __name__ == "__main__":
    storage.init()
    asyncio.run(main())
