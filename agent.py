"""Claude-агент: разбирает присланное и устанавливает по команде."""
import json

from persona import CHARACTER
import os
import re
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

HOME = Path.home()
WORKDIR = Path(__file__).parent / "data" / "work"
MODEL = os.getenv("AGENT_MODEL") or None

ANALYZE_PROMPT = """Ты — помощник, который ведёт каталог инструментов для Claude/AI-разработки.
Пользователь прислал материал (ссылку, текст, пересланный пост или файл). Твоя задача — ТОЛЬКО ИЗУЧИТЬ, ничего не устанавливать.

1. Открой ссылки (WebFetch), при необходимости поищи (WebSearch), прочитай файлы (Read).
2. Определи, что это: claude-skill | claude-plugin | mcp-server | cli-tool | library | saas-service | prompt/guide | other.
3. Составь конкретный план установки на macOS (Apple Silicon) для пользователя Claude Code:
   - skill → в ~/.claude/skills/<name>/ (папка с SKILL.md)
   - плагин → `claude plugin marketplace add ...` / `claude plugin install ...`
   - MCP-сервер → `claude mcp add --scope user ...`
   - CLI → brew / uv tool / npm -g
   - SaaS/сервис, требующий регистрации, оплаты или ключей → can_auto_install=false, опиши шаги для человека.
4. Оцени риск: кто автор, популярность, что делает код (сеть, доступ к файлам, curl|sh и т.п.).

Уже установленные skills: {installed}

Ответь в конце СТРОГО одним блоком ```json с полями:
{{"title": str, "kind": str, "summary": "2-4 предложения по-русски: что это и зачем",
  "install_plan": ["шаг 1", "шаг 2", ...], "can_auto_install": bool,
  "risk": "low|medium|high — пояснение", "already_installed": bool,
  "hamster_comment": "одна короткая живая реплика хомяка Стэша про эту находку (см. характер ниже), до 120 символов"}}

Характер для hamster_comment (только для этого поля, остальные поля — нейтрально и по делу):
{character}

Материал:
{material}
"""

INSTALL_PROMPT = """Установи инструмент на этот Mac по утверждённому плану. Пользователь подтвердил установку.

Название: {title}
Тип: {kind}
Исходный материал: {material}
План:
{plan}

Правила:
- Действуй только в рамках плана. Не трогай чужие настройки, не удаляй файлы, не используй sudo.
- Если шаг требует секретов, регистрации или оплаты — остановись и опиши, что сделать вручную.
- Клонируй/скачивай во временную папку {workdir}, итог клади в положенное место (~/.claude/skills, и т.п.).
- После установки проверь результат (ls, `claude mcp list`, `--version` и т.п.).

В конце ответь кратко по-русски: что установлено, где лежит, как пользоваться, что осталось сделать руками.
Последней строкой напиши ровно STATUS: installed | STATUS: manual | STATUS: failed
"""


def _installed_skills() -> str:
    d = HOME / ".claude" / "skills"
    if not d.exists():
        return "нет"
    return ", ".join(sorted(p.name for p in d.iterdir() if p.is_dir())) or "нет"


BUFFER = 20 * 1024 * 1024  # 20 МБ: большие файлы/страницы не роняют SDK


def _pdf_to_text(path: Path) -> Path | None:
    """PDF → .txt рядом (текст + все ссылки), чтобы агент не читал картинки."""
    txt = path.with_suffix(path.suffix + ".txt")
    if txt.exists():
        return txt
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts, links = [], []
        for i, page in enumerate(reader.pages, 1):
            parts.append(f"--- стр. {i} ---\n{page.extract_text() or ''}")
            for a in page.get("/Annots") or []:
                uri = (a.get_object().get("/A") or {}).get("/URI")
                if uri and uri not in links:
                    links.append(str(uri))
        if links:
            parts.append("--- ссылки в PDF ---\n" + "\n".join(links))
        txt.write_text("\n\n".join(parts), encoding="utf-8")
        return txt
    except Exception:  # noqa: BLE001
        return None


def _material(item: dict) -> str:
    s = item["raw_input"]
    fp = item.get("file_path")
    if fp:
        p = Path(fp)
        if p.suffix.lower() == ".pdf" and (txt := _pdf_to_text(p)):
            s += (f"\n\nПриложен PDF, его текст и ссылки извлечены в: {txt}\n"
                  "Читай ТОЛЬКО этот .txt, сам PDF не открывай.")
        else:
            s += f"\n\nПриложенный файл: {fp}"
    return s


async def _run(prompt: str, options: ClaudeAgentOptions) -> str:
    texts: list[str] = []
    result = ""
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    texts.append(block.text)
        elif isinstance(msg, ResultMessage):
            result = msg.result or ""
    return result or "\n".join(texts)


def _extract_json(text: str) -> dict:
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    raw = m[-1] if m else text[text.find("{") : text.rfind("}") + 1]
    return json.loads(raw)


async def analyze(item: dict) -> dict:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(
        model=MODEL,
        cwd=str(WORKDIR),
        allowed_tools=["WebFetch", "WebSearch", "Read", "Glob", "Grep"],
        disallowed_tools=["Bash", "Write", "Edit"],
        max_turns=20,
        max_buffer_size=BUFFER,
    )
    text = await _run(
        ANALYZE_PROMPT.format(installed=_installed_skills(), material=_material(item), character=CHARACTER),
        options,
    )
    return _extract_json(text)


async def install(item: dict) -> tuple[str, str]:
    """Возвращает (status, отчёт)."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    plan = item.get("install_plan") or "[]"
    try:
        plan_lines = "\n".join(f"- {s}" for s in json.loads(plan))
    except json.JSONDecodeError:
        plan_lines = plan
    options = ClaudeAgentOptions(
        model=MODEL,
        cwd=str(WORKDIR),
        allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch"],
        permission_mode="acceptEdits",
        add_dirs=[str(HOME / ".claude")],
        max_turns=40,
        max_buffer_size=BUFFER,
    )
    text = await _run(
        INSTALL_PROMPT.format(
            title=item.get("title"),
            kind=item.get("kind"),
            material=_material(item),
            plan=plan_lines,
            workdir=WORKDIR,
        ),
        options,
    )
    m = re.search(r"STATUS:\s*(installed|manual|failed)", text)
    status = m.group(1) if m else "failed"
    report = re.sub(r"\n?STATUS:.*$", "", text.strip())
    return status, report
