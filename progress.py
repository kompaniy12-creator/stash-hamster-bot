"""Живой прогресс в Telegram: одно сообщение, которое обновляется, пока агент работает.

    prog = Progress(bot, chat_id, message_id, "⌨️ Деплою #6 «tool»", expected=120)
    await prog.start()
    ... agent.install(item, on_tool=prog.event) ...
    await prog.finish(ok=True)

Шкала растёт по времени (асимптотически к ~95%) и подталкивается каждым действием агента.
Настоящий процент неизвестен — агент сам решает, сколько шагов делать, — поэтому до 100% доходит
только по факту завершения. Сообщение правится раз в ~2.5 с (лимиты Telegram это спокойно держат).
"""
import asyncio
import html
import math
import time
from urllib.parse import urlparse

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
WIDTH = 14
TICK = 2.5


def describe_tool(name: str, args: dict) -> str | None:
    """Короткая человекочитаемая строка о том, что делает агент. Ничего длинного и ничего похожего на секреты."""
    args = args or {}
    if name == "Bash":
        cmd = " ".join(str(args.get("command", "")).split())
        return "$ " + (cmd[:58] + "…" if len(cmd) > 58 else cmd)
    if name == "WebFetch":
        host = urlparse(str(args.get("url", ""))).netloc or "сеть"
        return f"🌐 читаю {host}"
    if name == "WebSearch":
        q = str(args.get("query", ""))
        return f"🔎 ищу «{q[:45]}»"
    if name in ("Read", "Glob", "Grep"):
        p = str(args.get("file_path") or args.get("path") or args.get("pattern") or "")
        return f"📄 смотрю {p.rsplit('/', 1)[-1][:45]}"
    if name in ("Write", "Edit"):
        p = str(args.get("file_path", ""))
        return f"✍️ пишу {p.rsplit('/', 1)[-1][:45]}"
    return f"⚙️ {name}"


class Progress:
    def __init__(self, bot, chat_id: int, message_id: int, title: str, expected: float = 90):
        self.bot, self.chat_id, self.message_id = bot, chat_id, message_id
        self.title, self.expected = title, expected
        self.t0 = time.monotonic()
        self.steps = 0
        self.log: list[str] = []
        self.frame = 0
        self._task: asyncio.Task | None = None
        self._last_text = ""

    # --- события от агента ---
    def event(self, name: str, args: dict) -> None:
        line = describe_tool(name, args)
        if line:
            self.steps += 1
            self.log = (self.log + [line])[-3:]

    # --- отрисовка ---
    def _pct(self) -> int:
        t = time.monotonic() - self.t0
        by_time = 1 - math.exp(-t / self.expected)          # 63% к ожидаемому времени
        by_steps = 1 - math.exp(-self.steps / 12)            # каждый шаг подталкивает
        return int(min(95, 100 * (0.65 * by_time + 0.35 * by_steps)))

    def _render(self, pct: int, head: str, spin: str) -> str:
        filled = round(WIDTH * pct / 100)
        bar = "▰" * filled + "▱" * (WIDTH - filled)
        secs = int(time.monotonic() - self.t0)
        el = f"{secs // 60}:{secs % 60:02d}"
        lines = [f"<b>{html.escape(head)}</b>",
                 f"<code>{bar} {pct:>3}%</code> {spin}",
                 f"<i>⏱ {el} · шагов: {self.steps}</i>"]
        if self.log:
            lines.append("<code>" + html.escape("\n".join(self.log)) + "</code>")
        return "\n".join(lines)

    async def _edit(self, text: str) -> None:
        if text == self._last_text:
            return
        try:
            await self.bot.edit_message_text(text, self.chat_id, self.message_id, parse_mode="HTML",
                                             disable_web_page_preview=True)
            self._last_text = text
        except Exception:  # noqa: BLE001  (message not modified, флуд-лимит и т.п. — пропускаем кадр)
            pass

    async def _loop(self) -> None:
        while True:
            self.frame += 1
            await self._edit(self._render(self._pct(), self.title, SPIN[self.frame % len(SPIN)]))
            await asyncio.sleep(TICK)

    async def start(self) -> "Progress":
        self._task = asyncio.create_task(self._loop())
        return self

    async def finish(self, ok: bool = True, head: str | None = None) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._edit(self._render(100 if ok else self._pct(), head or self.title, "✅" if ok else "❌"))
