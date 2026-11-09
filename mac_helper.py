"""Грузчик на Mac: забирает одобренные в облаке запасы и устанавливает их на этот компьютер.

Запускается launchd каждые 2 минуты. Читает зашифрованную норку из ветки norka-data,
ставит то, что ты одобрил кнопкой, пишет отчёт в Telegram и кладёт результат в results/,
откуда облачный хомяк его заберёт и обновит каталог.
"""
import asyncio
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

import agent  # noqa: E402
import crypto_store  # noqa: E402
import persona as P  # noqa: E402
from telegram import Bot  # noqa: E402

BRANCH = "norka-data"
WT = ROOT / "data" / "norka-sync"
DONE = ROOT / "data" / "helper_done.json"
LOCK = ROOT / "data" / "helper.lock"


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def sync() -> None:
    git("fetch", "origin", BRANCH)
    if not WT.exists():
        git("worktree", "add", "-f", "-B", f"{BRANCH}-helper", str(WT), f"origin/{BRANCH}")
    git("reset", "--hard", f"origin/{BRANCH}", cwd=WT)


def push_result(name: str, payload: dict) -> None:
    f = WT / "results" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(crypto_store.encrypt_bytes(json.dumps(payload, ensure_ascii=False).encode()))
    git("add", str(f), cwd=WT)
    git("-c", "user.name=stash-mac-helper", "-c", "user.email=stash@localhost",
        "commit", "-m", f"🐹 результат установки #{payload['id']}", cwd=WT)
    for _ in range(5):
        if git("pull", "--rebase", "origin", BRANCH, cwd=WT, check=False).returncode == 0 and \
           git("push", "origin", f"HEAD:{BRANCH}", cwd=WT, check=False).returncode == 0:
            return
    raise RuntimeError("не смог запушить результат")


async def main() -> None:
    sync()
    enc = WT / "norka.enc"
    if not enc.exists():
        return
    state = json.loads(crypto_store.decrypt_bytes(enc.read_bytes()))
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    todo = [it for it in state["items"].values()
            if it["status"] == "approved" and f"{it['id']}@{it.get('approved_at')}" not in done]
    if not todo:
        return
    tg = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
    chat = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
    async with tg:
        for it in todo:
            key = f"{it['id']}@{it.get('approved_at')}"
            await tg.send_message(chat, P.installing(it["id"], it.get("title") or "без названия"))
            try:
                status, report = await agent.install(it)
            except Exception as e:  # noqa: BLE001
                status, report = "failed", str(e)
            text = f"{P.install_head(status)} · #{it['id']} {it.get('title') or ''}\n\n{report}"
            await tg.send_message(chat, text[:3900])
            push_result(f"{it['id']}-{dt.datetime.now():%Y%m%d%H%M%S}.json.enc",
                        {"id": it["id"], "approved_at": it.get("approved_at"),
                         "status": status, "report": report})
            done.add(key)
            DONE.write_text(json.dumps(sorted(done)))


if __name__ == "__main__":
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)  # предыдущий запуск ещё ставит
        asyncio.run(main())
