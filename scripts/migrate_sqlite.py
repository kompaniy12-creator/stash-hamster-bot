"""Одноразово: перенести старый data/inbox.db (SQLite) в data/norka.json."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import storage  # noqa: E402

db = Path(__file__).resolve().parent.parent / "data" / "inbox.db"
if storage.DATA_PATH.exists():
    sys.exit(f"{storage.DATA_PATH} уже есть — не трогаю")
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute("SELECT * FROM items ORDER BY id")]
state = {"next_id": max((r["id"] for r in rows), default=0) + 1, "items": {}, "meta": {}}
for r in rows:
    item = {"id": r["id"], "created_at": r["created_at"], "raw_input": r["raw_input"],
            "file_path": r["file_path"], "status": r["status"], **{f: r.get(f) for f in storage.FIELDS}}
    state["items"][str(r["id"])] = item
storage._save(state)
print(f"Перенесено {len(rows)} запасов → {storage.DATA_PATH}")
