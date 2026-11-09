"""Удаление по кнопке. Только то, что есть в свежей описи, и самым мягким способом:
  skill  → папка переезжает в Корзину (~/.Trash), можно вернуть;
  plugin → `claude plugin uninstall <id>` (поставить обратно можно в любой момент);
  mcp    → конфиг сохраняется в data/backups/ (в нём бывают ключи, файл только для тебя), потом `claude mcp remove`.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import inventory

HOME = Path.home()
BACKUPS = Path(__file__).parent / "data" / "backups"
SAFE = re.compile(r"^[A-Za-z0-9_.@:+-]{1,120}$")


def remove(target: str) -> tuple[bool, str]:
    """target = 'skill:<dir>' | 'plugin:<name@market>' | 'mcp:<name>'"""
    typ, _, name = target.partition(":")
    if typ not in ("skill", "plugin", "mcp") or not SAFE.match(name or ""):
        return False, "непонятная цель"
    inv = inventory.collect()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if typ == "skill":
        if name in inventory.SYSTEM_DIRS or name not in {s["dir"] for s in inv["skills"]}:
            return False, "такого скилла уже нет"
        src = HOME / ".claude" / "skills" / name
        if src.is_symlink() or not src.resolve().is_relative_to((HOME / ".claude" / "skills").resolve()):
            return False, "это ссылка на чужую папку — убери вручную"
        dst = HOME / ".Trash" / f"{name}-stash-{stamp}"
        shutil.move(str(src), str(dst))
        return True, f"папка переехала в Корзину ({dst.name}), можно вернуть"
    if typ == "plugin":
        if name not in {p["id"] for p in inv["plugins"]}:
            return False, "такого плагина уже нет"
        r = subprocess.run(["claude", "plugin", "uninstall", name], capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stdout or r.stderr).strip()[-300:] or "готово"
    if name not in {m["name"] for m in inv["mcp"]}:
        return False, "такого MCP уже нет"
    cfg = (json.loads((HOME / ".claude.json").read_text()).get("mcpServers") or {}).get(name)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    bk = BACKUPS / f"mcp-{name}-{stamp}.json"
    bk.write_text(json.dumps({name: cfg}, ensure_ascii=False, indent=1))
    os.chmod(bk, 0o600)
    r = subprocess.run(["claude", "mcp", "remove", name, "-s", "user"], capture_output=True, text=True, timeout=120)
    return r.returncode == 0, f"конфиг сохранён в data/backups/{bk.name}. " + (r.stdout or r.stderr).strip()[-200:]
