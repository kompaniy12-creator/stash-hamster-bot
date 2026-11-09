"""Команды бота в облачном режиме, без сети: подменяем отправку сообщений."""
import asyncio

import pytest
from telegram import Message, Update, User
from telegram.ext import ExtBot

import bot
import cloud_run
import storage


@pytest.fixture
def app(monkeypatch):
    sent = []

    async def reply_text(self, text, **kw):
        sent.append(text)

    async def send_message(self, chat_id, text, **kw):
        sent.append(text)

    monkeypatch.setattr(Message, "reply_text", reply_text)
    monkeypatch.setattr(ExtBot, "send_message", send_message)
    a = bot.build_app()
    a._initialized = True
    a.bot._bot_user = User(1, "Stash", True, username="stash_skills_bot")
    a.sent = sent
    return a


def cmd(app, uid, text):
    u = Update.de_json({"update_id": uid, "message": {
        "message_id": 0, "date": 0, "text": text, "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "S"},
        "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]}}, app.bot)

    async def run():
        await app.process_update(u)
        while bot.PENDING:
            batch, bot.PENDING[:] = bot.PENDING[:], []
            await asyncio.gather(*batch)
    asyncio.run(run())


def test_remove_queues_only_valid_targets(app):
    cmd(app, -1, "/remove skill:ab-testing plugin:x@m ../evil")
    assert [q["target"] for q in storage.meta_get("remove_queue")] == ["skill:ab-testing", "plugin:x@m"]


def test_update_queue(app):
    cmd(app, -2, "/update plugin:c-review@trailofbits")
    assert storage.meta_get("update_queue")[0]["target"] == "plugin:c-review@trailofbits"


def test_replace_marks_targets(app):
    i = storage.add("x")
    storage.update(i, status="ready", title="new", overlaps=[
        {"target": "plugin:old@m", "name": "old", "relation": "replace", "note": ""}])
    cmd(app, -3, f"/replace {i}")
    it = storage.get(i)
    assert it["status"] == "approved" and "plugin:old@m" in it["replace_targets"]


def test_skip(app):
    i = storage.add("x")
    cmd(app, -4, f"/skip {i}")
    assert storage.get(i)["status"] == "skipped"


def test_stranger_is_ignored(app):
    u = Update.de_json({"update_id": -5, "message": {
        "message_id": 0, "date": 0, "text": "/remove skill:x", "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "evil"},
        "entities": [{"type": "bot_command", "offset": 0, "length": 7}]}}, app.bot)
    asyncio.run(app.process_update(u))
    assert not storage.meta_get("remove_queue")


def test_catalog_snapshot_shape():
    i = storage.add("x")
    storage.update(i, title="claude-seo (x)", status="installed", install_plan=["a"], can=["b"], install_names=["claude-seo"])
    snap = cloud_run.catalog_snapshot()
    assert set(snap) == {"items", "remove_queue", "update_queue"}
    assert snap["items"][0]["plan"] == ["a"] and "claude-seo" in snap["items"][0]["names"]
