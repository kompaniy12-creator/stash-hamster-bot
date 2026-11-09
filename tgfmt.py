"""Markdown от агента → HTML для Telegram + отправка длинных текстов частями.

Telegram понимает только узкий набор тегов (<b>, <i>, <code>, <pre>, <a>, <blockquote>),
поэтому заголовки, списки и таблицы превращаем в читаемый текст, а не в разметку.
"""
import html
import textwrap
import re

LIMIT = 3800  # запас до лимита Telegram в 4096 символов


def _inline(s: str) -> str:
    """Одна строка без блоков кода: экранируем и размечаем `код`, **жирный**, *курсив*, [ссылки](url)."""
    codes: list[str] = []

    def keep(m):
        codes.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    p = re.sub(r"`([^`\n]+)`", keep, s)  # код прячем, чтобы внутри него ничего не размечалось
    p = html.escape(p, quote=False)
    p = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', p)
    p = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p)
    p = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"<b>\1</b>", p)
    p = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<i>\1</i>", p)
    return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], p)


def _line(line: str) -> str:
    m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
    if m:  # заголовок → жирная строка
        return f"<b>{_inline(m.group(1).strip('# '))}</b>"
    m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
    if m:  # маркированный список
        indent = "   " * (len(m.group(1)) // 2)
        return f"{indent}• {_inline(m.group(2))}"
    if re.match(r"^\s*\|?\s*:?-{3,}", line):  # разделитель таблицы
        return ""
    if line.strip().startswith("|") and line.strip().endswith("|"):  # строка таблицы
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        return " · ".join(_inline(c) for c in cells if c)
    if re.match(r"^\s*>\s?", line):
        quote = re.sub(r"^\s*>\s?", "", line)
        return f"<blockquote>{_inline(quote)}</blockquote>"
    if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
        return ""
    return _inline(line)


def _blocks(md: str) -> list[str]:
    """Режем текст на абзацы и блоки кода (блок кода никогда не делится пополам)."""
    blocks, buf, in_code, code = [], [], False, []
    for line in md.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            if in_code:
                body = html.escape(textwrap.dedent("\n".join(code)))
                blocks.append(f"<pre>{body}</pre>")
                code, in_code = [], False
            else:
                if buf:
                    blocks.append("\n".join(buf)); buf = []
                in_code = True
            continue
        if in_code:
            code.append(line)
        elif line.strip() == "":
            if buf:
                blocks.append("\n".join(buf)); buf = []
        else:
            buf.append(_line(line))
    if in_code and code:
        blocks.append(f"<pre>{html.escape(chr(10).join(code))}</pre>")
    if buf:
        blocks.append("\n".join(buf))
    return [b for b in blocks if b.strip()]


def md_to_html(md: str) -> str:
    return "\n\n".join(_blocks(md or ""))


def chunks(md: str, limit: int = LIMIT) -> list[str]:
    """HTML-куски не длиннее limit, разрез только между абзацами."""
    out, cur = [], ""
    for b in _blocks(md or ""):
        if len(b) > limit:  # огромный абзац/код — режем по строкам, теги внутри строки не рвём
            is_pre = b.startswith("<pre>")
            inner = b[5:-6] if is_pre else b
            piece = ""
            for ln in inner.split("\n"):
                if len(piece) + len(ln) + 12 > limit and piece:
                    out.append(f"<pre>{piece}</pre>" if is_pre else piece)
                    piece = ""
                piece += ("\n" if piece else "") + ln[:limit - 12]
            if piece:
                b = f"<pre>{piece}</pre>" if is_pre else piece
            else:
                continue
        if cur and len(cur) + 2 + len(b) > limit:
            out.append(cur); cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        out.append(cur)
    return out or [""]


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))


async def send_md(bot, chat_id: int, md: str, reply_markup=None, edit_message_id: int | None = None):
    """Отправить markdown-текст красиво. Первую часть можно вписать в существующее сообщение.
    Кнопки вешаются на последнюю часть. Если Telegram не принял разметку — шлём ту же часть простым текстом."""
    parts = chunks(md)
    last = None
    for i, part in enumerate(parts):
        kb = reply_markup if i == len(parts) - 1 else None
        for mode, text in (("HTML", part), (None, _strip_tags(part))):
            try:
                if i == 0 and edit_message_id:
                    last = await bot.edit_message_text(text, chat_id, edit_message_id, parse_mode=mode,
                                                       reply_markup=kb, disable_web_page_preview=True)
                else:
                    last = await bot.send_message(chat_id, text, parse_mode=mode, reply_markup=kb,
                                                  disable_web_page_preview=True)
                break
            except Exception:  # noqa: BLE001
                if mode is None:
                    raise
    return last
