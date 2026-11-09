"""Норка: JSON-каталог всего, что прислали хомяку.

Один файл (по умолчанию data/norka.json, путь можно задать через STASH_DATA).
Каждая операция читает и пишет файл целиком — данных мало, зато просто и одинаково
работает и на Mac, и в GitHub Actions.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

DATA_PATH = Path(os.getenv("STASH_DATA") or Path(__file__).parent / "data" / "norka.json")

STATUSES = {
    "new": "🆕 новый",
    "analyzing": "🔎 анализ",
    "ready": "📥 ждёт установки",
    "manual": "✋ ставить вручную",
    "approved": "🚚 ждёт Mac",
    "installing": "⚙️ ставится",
    "installed": "✅ установлен",
    "failed": "❌ ошибка",
    "skipped": "🗑 не нужно",
}

FIELDS = ("title", "kind", "summary", "install_plan", "can_auto_install", "risk",
          "log", "comment", "last_nudged", "approved_at", "can", "install_names",
          "overlaps", "replace_targets")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> dict:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return {"next_id": 1, "items": {}, "meta": {}}


def _save(state: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA_PATH)


def init() -> None:
    if not DATA_PATH.exists():
        _save(_load())


def add(raw_input: str, file_path: str | None = None) -> int:
    st = _load()
    i = st["next_id"]
    st["next_id"] = i + 1
    st["items"][str(i)] = {"id": i, "created_at": _now(), "raw_input": raw_input,
                           "file_path": file_path, "status": "new", **{f: None for f in FIELDS}}
    _save(st)
    return i


def get(item_id: int) -> dict | None:
    it = _load()["items"].get(str(item_id))
    return dict(it) if it else None


def update(item_id: int, **fields) -> None:
    if not fields:
        return
    for k, v in list(fields.items()):
        if isinstance(v, (list, dict)):
            fields[k] = json.dumps(v, ensure_ascii=False)
    st = _load()
    it = st["items"].get(str(item_id))
    if it is None:
        return
    it.update(fields)
    _save(st)


def list_items(statuses: list[str] | None = None, limit: int = 50) -> list[dict]:
    items = sorted(_load()["items"].values(), key=lambda x: x["id"], reverse=True)
    if statuses:
        items = [x for x in items if x["status"] in statuses]
    return [dict(x) for x in items[:limit]]


def stats() -> dict:
    d: dict = {}
    for it in _load()["items"].values():
        d[it["status"]] = d.get(it["status"], 0) + 1
    d["total"] = sum(d.values())
    return d


def nudge_candidate(min_age_days: int = 2, every_days: int = 3) -> dict | None:
    """Самый старый неустановленный запас, о котором давно не напоминали."""
    now = datetime.now()
    cands = [
        it for it in _load()["items"].values()
        if it["status"] in ("ready", "manual")
        and datetime.fromisoformat(it["created_at"]) <= now - timedelta(days=min_age_days)
        and (not it.get("last_nudged")
             or datetime.fromisoformat(it["last_nudged"]) <= now - timedelta(days=every_days))
    ]
    return dict(min(cands, key=lambda x: x["created_at"])) if cands else None


# --- служебное состояние (offset Telegram, даты напоминаний) ---
def meta_get(key: str, default=None):
    return _load()["meta"].get(key, default)


def meta_set(key: str, value) -> None:
    st = _load()
    st["meta"][key] = value
    _save(st)
