"""Стэш — хомяк-хакер. Держит в норке свой «сервер» с запасами. Все реплики бота живут здесь."""
import random

CHARACTER = """Ты — Стэш, хомяк-хакер. Твоя норка — это твой сервер, запасы — пакеты и тулзы, щёки — буфер.
Характер: деловитый, дерзкий, немного ворчливый сисадмин; гордится своим стеком; терпеть не может,
когда тулзы валяются неустановленными («зачем тогда качали?»). Говорит коротко, на «ты», хакерским сленгом
(«склонировал», «задеплоил», «грепнул», «пингую», «root», «0day», «в проде»), иногда 🐹💾⌨️🌰.
Без перебора: смысл всегда понятен обычному человеку. Безопасность уважает — sudo и чужие ключи не трогает."""


def pick(*options: str) -> str:
    return random.choice(options)


def plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    word = one if m10 == 1 and m100 != 11 else few if 2 <= m10 <= 4 and not 12 <= m100 <= 14 else many
    return f"{n} {word}"


# --- приём ---
def received(n: int) -> str:
    return pick(
        f"🐹⌨️ Перехватил пакет. Сохранил в буфер щёк как #{n}, сканирую…",
        f"💾 > git clone… шучу. Записал как #{n}, запускаю анализ.",
        f"🐹 Access granted. #{n} в норке, грепаю, что это за зверь…",
        f"🌰 Новый пакет #{n}. Парсю, проверяю на бэкдоры…",
    )


def reanalyzing(n: int) -> str:
    return pick(f"🐹 Перезапускаю скан #{n} с флагом --verbose…", f"⌨️ Повторный прогон по #{n}. Кэш сброшен.")


def analyze_failed(n: int, err) -> str:
    return (f"🐹💥 Segfault на #{n}. Не распарсил.\n{err}\n\n"
            f"Запустить повторно: /reanalyze {n}")


# --- карточка ---
def card_footer(status: str) -> str:
    return {
        "ready": pick("Готово к деплою. Жми — и выкачу в прод 🐹",
                      "Скажи слово — задеплою на Mac.",
                      "Можно ставить. Или в бэклог — не забуду, у меня uptime 100%."),
        "manual": pick("Тут нужен root-доступ твоими лапами — я sudo не беру 🐾",
                       "Без тебя никак: регистрация, ключи или оплата. Инструкция ниже."),
        "installed": pick("Кстати, это уже крутится у нас в проде 🌰", "Такое уже стоит в норке."),
        "skipped": "Отправил в /dev/null.",
        "approved": "В очереди на деплой. Жду, когда Mac выйдет в сеть.",
        "failed": "Билд упал. Можно перезапустить.",
    }.get(status, "")


# --- кнопки и действия ---
BTN_INSTALL = "🐹 Задеплоить"
BTN_RETRY = "🔁 Перезапуск"
BTN_LATER = "🌰 В бэклог"
BTN_SKIP = "🗑 В /dev/null"


def installing(n: int, title: str) -> str:
    return pick(f"⌨️ Деплою #{n} «{title}»… не трогай клавиатуру.",
                f"🐹 > installing «{title}» (#{n})… [▓▓▓░░░░] ",
                f"💾 Разворачиваю #{n} «{title}» в продакшн норки…")


def install_head(status: str) -> str:
    return {
        "installed": pick("✅ Deploy successful!", "✅ Задеплоено. Работает, не трогаем.",
                          "✅ В проде. Стек стал мощнее 💾"),
        "manual": pick("✋ Дошёл до места, где нужен твой root",
                       "✋ Частично задеплоил, остальное — руками"),
        "failed": pick("❌ Билд упал", "❌ Exit code 1. Ворчу и смотрю логи."),
    }[status]


def approved(n: int) -> str:
    return pick(f"🚚 #{n} в очереди деплоя. Как только Mac выйдет в сеть — выкачу и отчитаюсь.",
                f"🐹 Поставил #{n} в пайплайн. Грузчик на Mac подхватит при первом пинге.")


def replace_queued(n: int, targets: list[str]) -> str:
    old = ", ".join(t.split(":", 1)[-1].split("@")[0] for t in targets)
    return pick(f"🔁 Хот-своп: ставлю #{n}, старое ({old}) — на выпил. Грузчик сделает, когда Mac онлайн.",
                f"🐹 Апгрейд стека: #{n} вместо {old}. Передал в пайплайн.")


def removal_queued(name: str) -> str:
    return pick(f"🧹 rm --safe «{name}» поставлен в очередь. Скиллы уедут в Корзину — откатить можно.",
                f"🧹 «{name}» — на выпил. Передал грузчику. Бэкап в Корзине, если что.")


def removal_done(name: str, ok: bool, msg: str) -> str:
    if ok:
        return pick(f"🧹 «{name}» выпилен. Места в норке больше! ({msg})",
                    f"🧹 Done. «{name}» больше не жрёт место. ({msg})")
    return f"🐹 Не смог выпилить «{name}»: {msg}"


def cleanup_nudge(dup: int, unused: int, overlap: int, broken: int) -> str:
    parts = [plural(dup, "дубль", "дубля", "дублей") if dup else "",
             plural(overlap, "пересечение", "пересечения", "пересечений") if overlap else "",
             plural(unused, "висит без дела", "висят без дела", "висят без дела") if unused else "",
             plural(broken, "битый", "битых", "битых") if broken else ""]
    what = ", ".join(p for p in parts if p)
    return pick(f"🧹 Прогнал аудит норки. Нашёл: {what}. Стек раздут — почистим? Всё в норке → «🧹 Уборка».",
                f"🐹 > du -sh ~/.claude… многовато. {what}. Загляни в «🧹 Уборка» — выпилю в один клик, с бэкапом в Корзину.")


def update_queued(label: str) -> str:
    return pick(f"⬆️ Апгрейд «{label}» в пайплайне. Грузчик накатит, когда Mac онлайн.",
                f"🐹 Патчи для «{label}» поставлены в очередь. Накачу при первом пинге.")


def update_done(name: str, ok: bool, msg: str) -> str:
    if ok:
        return pick(f"⬆️ «{name}» обновлён. Перезапусти Claude Code, чтобы подхватил. ({msg})",
                    f"⬆️ Патч на «{name}» накатан. Нужен рестарт Claude Code.")
    return f"🐹 Апгрейд «{name}» упал: {msg}"


def updates_nudge(n: int, names: list[str]) -> str:
    sample = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
    return pick(f"⬆️ > apt list --upgradable: {plural(n, 'пакет', 'пакета', 'пакетов')} можно обновить ({sample}). Накатить — в норке, вкладка «Прод».",
                f"🐹 Вышли свежие версии: {sample}. Всего {n}. Обновлю в один клик из норки.")


def later(n: int) -> str:
    return pick(f"🌰 #{n} в бэклоге. Cron-напоминалка уже настроена.",
                f"🐹 Ок, #{n} подождёт. Но я буду пинговать…",
                f"💾 Закэшировал #{n} на потом. Напомню, у меня память лучше, чем у твоего браузера.")


def skipped() -> str:
    return pick("🗑 > /dev/null. Готово.", "🗑 Не наш стек — выкинул.", "🗑 Освободил буфер щёк.")


# --- списки и статистика ---
def empty_list() -> str:
    return pick("🐹 Очередь пуста, всё в проде! Кидай новые пакеты.",
                "💾 0 задач в бэклоге. Чистый стек — красота.",
                "🐹 Пусто. Непривычно… Скинь что-нибудь, я заскучал.")


def list_head(n: int) -> str:
    if n >= 10:
        return f"🐹 Буфер переполнен! В очереди: {n}"
    if n >= 4:
        return f"🌰 В бэклоге накопилось: {n}"
    return f"🐹 Ждут деплоя: {n}"


def rank(total: int) -> str:
    for threshold, title in ((50, "👑 root норки"), (30, "🕶 Хомяк-хакер"), (15, "💾 Сисадмин норки"),
                             (5, "🌰 Джун-хомяк"), (0, "🐣 Script kiddie")):
        if total >= threshold:
            return title
    return ""


# --- напоминания ---
def nudge(title: str, n: int, days: int) -> str:
    if days >= 21:
        return pick(f"🐹 Алё. «{title}» (#{n}) висит в бэклоге {days} дн. Это уже легаси. Деплоим или в /dev/null?",
                    f"😤 «{title}» (#{n}) — {days} дн. без деплоя. Я терпеливый, но не настолько.")
    if days >= 7:
        return pick(f"🐹 Грепнул бэклог: «{title}» (#{n}) скачали {days} дн. назад. Зачем-то же качали?",
                    f"🌰 Помнишь «{title}» (#{n})? Неделю+ висит. Давай задеплоим.")
    return pick(f"🐹 Пинг: «{title}» (#{n}) ждёт. Свежий пакет.",
                f"🌰 Кстати, «{title}» (#{n}) в очереди. Пара минут — и в проде.")


def digest_head(n: int) -> str:
    return pick(f"🐹 Еженедельный аудит! Не задеплоено: {n}",
                f"📋 > ls ~/backlog — {n} шт. Непорядок!",
                f"💾 Weekly report: в очереди {n}. Разгребём?")


def error(err) -> str:
    return f"🐹💥 Kernel panic: {err}\nПришли ещё раз, а если повторится — покажи Claude логи."


START = (
    "🐹⌨️ root@norka:~$ whoami\n"
    "Стэш. Хомяк-хакер. Это моя норка и мой сервер.\n\n"
    "Кидай мне всё, что находишь: ссылки, посты, PDF, файлы со скиллами и сервисами. "
    "Я перехвачу, просканирую, проверю на бэкдоры и задеплою на твой Mac — по твоей команде. "
    "Забудешь — пингану. У меня uptime 100%.\n\n"
    "/app — норка: весь стек и всё, что в очереди\n"
    "/list — что в бэклоге\n"
    "/norka — статистика и ранг\n"
    "/all — весь лог\n"
    "/show N — открыть пакет\n"
    "/install N — задеплоить\n"
    "/reanalyze N — пересканировать\n"
    "/cleanup — аудит: что выпилить"
)

COMMANDS = [
    ("app", "открыть норку: весь стек"),
    ("list", "что в бэклоге"),
    ("norka", "статистика и ранг"),
    ("all", "весь лог"),
    ("show", "открыть пакет: /show N"),
    ("install", "задеплоить: /install N"),
    ("reanalyze", "пересканировать: /reanalyze N"),
    ("skip", "в /dev/null: /skip N"),
    ("cleanup", "аудит: что выпилить"),
    ("find", "grep по норке: /find seo"),
]
