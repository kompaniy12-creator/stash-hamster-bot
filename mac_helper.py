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
import tgfmt  # noqa: E402
from telegram import Bot  # noqa: E402

# ключ дашборда читаем сразу: agent.scrub_env() потом уберёт секреты из окружения
DASH_SECRET = os.getenv("STASH_DASH_SECRET", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0") or 0)
os.environ.pop("STASH_DASH_SECRET", None)
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
    removals = [r for r in (state.get("meta", {}).get("remove_queue") or [])
                if f"rm:{r['target']}@{r['requested_at']}" not in done]
    if not todo and not removals:
        return
    import remover  # noqa: PLC0415
    tg = Bot(TG_TOKEN)
    chat = TG_CHAT
    short = lambda t: t.split(":", 1)[-1].split("@")[0]  # noqa: E731

    def mark(key: str) -> None:
        done.add(key)
        DONE.write_text(json.dumps(sorted(done)))

    async with tg:
        for it in todo:
            key = f"{it['id']}@{it.get('approved_at')}"
            await tg.send_message(chat, P.installing(it["id"], it.get("title") or "без названия"))
            try:
                status, report = await agent.install(it)
            except Exception as e:  # noqa: BLE001
                status, report = "failed", str(e)
            targets = it.get("replace_targets") or []
            if isinstance(targets, str):
                targets = json.loads(targets or "[]")
            if status == "installed" and targets:  # «поставить вместо старого»: старое выносим только после успеха
                for t in targets:
                    ok, msg = remover.remove(t)
                    report += "\n\n" + P.removal_done(short(t), ok, msg)
            await tgfmt.send_md(tg, chat, f"**{P.install_head(status)} · #{it['id']} {it.get('title') or ''}**\n\n{report}")
            push_result(f"{it['id']}-{dt.datetime.now():%Y%m%d%H%M%S}.json.enc",
                        {"id": it["id"], "approved_at": it.get("approved_at"),
                         "status": status, "report": report})
            mark(key)
        for r in removals:
            ok, msg = remover.remove(r["target"])
            await tg.send_message(chat, P.removal_done(short(r["target"]), ok, msg))
            push_result(f"rm-{dt.datetime.now():%Y%m%d%H%M%S%f}.json.enc",
                        {"kind": "removal", "id": 0, "target": r["target"], "requested_at": r["requested_at"],
                         "ok": ok, "msg": msg})
            mark(f"rm:{r['target']}@{r['requested_at']}")
        INV_SHA.unlink(missing_ok=True)  # что-то поменялось — пересобрать опись


INV_SHA = ROOT / "data" / "inventory.sha"
NUDGE_FILE = ROOT / "data" / "cleanup_nudge.json"


async def push_inventory() -> None:
    """Опись установленного + советы по уборке → дашборд (только если что-то поменялось)."""
    url = os.getenv("STASH_DASH_URL", "").rstrip("/")
    secret = DASH_SECRET
    if not url or not secret:
        return
    import hashlib

    import httpx

    import cleanup
    import inventory
    inv = inventory.collect()
    inv = await inventory.enrich(inv)
    inv["cleanup"] = await cleanup.suggestions(inv)
    body = json.dumps(inv, ensure_ascii=False, sort_keys=True)
    sha = hashlib.sha256(body.encode()).hexdigest()
    if INV_SHA.exists() and INV_SHA.read_text() == sha:
        return
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{url}/snapshot/inventory", content=body.encode(),
                         headers={"authorization": f"Bearer {secret}", "content-type": "application/json"})
    if r.status_code != 200:
        print(f"inventory push failed: {r.status_code}")
        return
    INV_SHA.write_text(sha)
    await maybe_nudge_cleanup(inv["cleanup"], url)


async def maybe_nudge_cleanup(sug: list[dict], url: str) -> None:
    """Раз в неделю хомяк ворчит, если в норке пыльно."""
    if not sug:
        return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo  # noqa: PLC0415
    last = json.loads(NUDGE_FILE.read_text()) if NUDGE_FILE.exists() else {}
    ids = sorted(s["id"] for s in sug)
    if last.get("at") and (dt.datetime.now() - dt.datetime.fromisoformat(last["at"])).days < 7:
        return
    if last.get("ids") == ids:
        return  # ничего нового с прошлого раза
    cnt = {k: sum(1 for s in sug if s["kind"] == k) for k in ("duplicate", "unused", "overlap", "broken")}
    tg = Bot(TG_TOKEN)
    async with tg:
        await tg.send_message(TG_CHAT, P.cleanup_nudge(cnt["duplicate"], cnt["unused"], cnt["overlap"], cnt["broken"]),
                              reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                                  "🧹 Открыть уборку", web_app=WebAppInfo(f"{url}/app#cleanup"))]]))
    NUDGE_FILE.write_text(json.dumps({"at": dt.datetime.now().isoformat(), "ids": ids}))


if __name__ == "__main__":
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)  # предыдущий запуск ещё ставит
        asyncio.run(main())
        asyncio.run(push_inventory())
