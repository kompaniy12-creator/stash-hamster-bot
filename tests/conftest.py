"""Общая настройка: фейковые секреты и отдельный файл норки на каждый тест."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:" + "a" * 35)
os.environ.setdefault("TELEGRAM_ALLOWED_USER_ID", "42")
os.environ.setdefault("STASH_MODE", "cloud")
os.environ.setdefault("STASH_QUEUE_URL", "https://example.invalid")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_storage(tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "DATA_PATH", tmp_path / "norka.json")
    storage.init()
    yield storage
