"""Чем ты реально пользуешься: считаем по истории Claude Code (~/.claude/projects/**/*.jsonl).

Считаются: вызовы инструмента Skill, набранные /команды, инструменты MCP (mcp__<сервер>__…),
субагенты плагинов (subagent_type "плагин:агент"). Читаем только эти метки, содержимое бесед не трогаем.
Результат кешируется по размеру и mtime файлов, поэтому повторный проход быстрый.
"""
import json
import re
import time
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
CACHE = Path(__file__).parent / "data" / "usage_cache.json"
DAYS = 90

_PATTERNS = {
    "skill": re.compile(rb'"name":"Skill","input":\{"skill":"([^"]{1,120})"'),
    "command": re.compile(rb"<command-name>/([^<\s]{1,120})</command-name>"),
    "mcp": re.compile(rb'"name":"mcp__([A-Za-z0-9_.-]{1,80}?)__'),
    "agent": re.compile(rb'"subagent_type":"([^"]{1,120})"'),
}
_TS = re.compile(rb'"timestamp":"(\d{4}-\d\d-\d\dT[\d:.]+)Z?"')


def _scan(path: Path) -> dict:
    """{kind: {name: [count, last_ts]}} для одного файла."""
    out: dict = {k: {} for k in _PATTERNS}
    last_ts = None
    with path.open("rb") as f:
        for line in f:
            if b'"timestamp"' in line:
                m = _TS.search(line)
                if m:
                    last_ts = m.group(1).decode()
            for kind, rx in _PATTERNS.items():
                for m in rx.finditer(line):
                    name = m.group(1).decode("utf-8", "replace")
                    c = out[kind].setdefault(name, [0, None])
                    c[0] += 1
                    if last_ts and (c[1] is None or last_ts > c[1]):
                        c[1] = last_ts
    return out


def collect() -> dict:
    """{"skill": {name: {"count", "last"}}, "command": …, "mcp": …, "agent": …, "days": 90}"""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    fresh = {}
    cutoff = time.time() - DAYS * 86400
    total: dict = {k: {} for k in _PATTERNS}
    for f in PROJECTS.glob("**/*.jsonl") if PROJECTS.exists() else []:
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff:
            continue
        key = f"{f}|{st.st_size}|{int(st.st_mtime)}"
        res = cache.get(key) or _scan(f)
        fresh[key] = res
        for kind, names in res.items():
            for name, (cnt, last) in names.items():
                t = total[kind].setdefault(name, {"count": 0, "last": None})
                t["count"] += cnt
                if last and (t["last"] is None or last > t["last"]):
                    t["last"] = last
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(fresh))
    total["days"] = DAYS
    return total


def for_name(usage: dict, name: str, plugin: str | None = None) -> dict:
    """Сколько раз использовали скилл/команду: учитывает и «name», и «plugin:name»."""
    keys = {name, f"{plugin}:{name}"} if plugin else {name}
    cnt, last = 0, None
    for kind in ("skill", "command", "agent"):
        for k in keys:
            u = usage.get(kind, {}).get(k)
            if u:
                cnt += u["count"]
                if u["last"] and (last is None or u["last"] > last):
                    last = u["last"]
    return {"count": cnt, "last": last}


if __name__ == "__main__":
    t = time.time()
    u = collect()
    print(f"{time.time() - t:.1f}s", {k: len(v) for k, v in u.items() if isinstance(v, dict)})
    print(sorted(((v["count"], k) for k, v in u["skill"].items()), reverse=True)[:5])
