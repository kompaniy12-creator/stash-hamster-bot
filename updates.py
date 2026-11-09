"""Проверка обновлений: плагины (по манифестам маркетплейсов) и личные скиллы, которые git-репозитории.

Раз в неделю: `claude plugin marketplace update` (подтягивает манифесты), потом сравниваем версии.
Ставит обновления не этот модуль, а грузчик по твоей кнопке: apply(target).
"""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
PLUGINS = HOME / ".claude" / "plugins"
CACHE = Path(__file__).parent / "data" / "updates.json"
EVERY_DAYS = 7


def _run(*cmd: str, cwd: Path | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _ver(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def _plugin_json_version(root: Path) -> str | None:
    for f in (root / ".claude-plugin" / "plugin.json", root / "plugin.json"):
        if f.exists():
            try:
                return json.loads(f.read_text()).get("version")
            except ValueError:
                return None
    return None


def _latest_plugin_versions() -> dict:
    """{ "name@market": {"version": "...", "sha": "..."} } по локальным манифестам."""
    known = json.loads((PLUGINS / "known_marketplaces.json").read_text()) if (PLUGINS / "known_marketplaces.json").exists() else {}
    out = {}
    for market, info in known.items():
        loc = Path(info.get("installLocation", ""))
        mf = loc / ".claude-plugin" / "marketplace.json"
        if not mf.exists():
            continue
        try:
            plugins = json.loads(mf.read_text()).get("plugins", [])
        except ValueError:
            continue
        head = _run("git", "rev-parse", "HEAD", cwd=loc).stdout.strip() if (loc / ".git").exists() else None
        for p in plugins:
            src = p.get("source")
            ver, sha = p.get("version"), None
            if isinstance(src, str):
                ver = ver or _plugin_json_version((loc / src).resolve())
                sha = head
            elif isinstance(src, dict):
                ver = ver or src.get("ref")
                sha = src.get("sha")
            out[f"{p.get('name')}@{market}"] = {"version": ver, "sha": sha}
    return out


def check(force: bool = False) -> list[dict]:
    """Список доступных обновлений; кешируется на неделю."""
    if CACHE.exists() and not force:
        c = json.loads(CACHE.read_text())
        if (datetime.now() - datetime.fromisoformat(c["at"])).days < EVERY_DAYS:
            return c["updates"]
    _run("claude", "plugin", "marketplace", "update", timeout=300)
    latest = _latest_plugin_versions()
    installed = json.loads((PLUGINS / "installed_plugins.json").read_text()).get("plugins", {})
    ups = []
    for pid, installs in installed.items():
        inst = installs[0] if isinstance(installs, list) and installs else installs
        cur, lat = inst.get("version"), latest.get(pid) or {}
        if lat.get("version") and _ver(lat["version"]) > _ver(cur):
            ups.append({"id": f"plugin:{pid}", "type": "plugin", "name": pid.split("@")[0],
                        "current": cur, "latest": lat.get("version")})
    for d in sorted((HOME / ".claude" / "skills").glob("*/.git")):
        repo = d.parent
        if _run("git", "fetch", "--quiet", cwd=repo, timeout=60).returncode != 0:
            continue
        behind = _run("git", "rev-list", "--count", "HEAD..@{u}", cwd=repo).stdout.strip()
        if behind.isdigit() and int(behind) > 0:
            ups.append({"id": f"skill:{repo.name}", "type": "skill", "name": repo.name,
                        "current": _run("git", "rev-parse", "--short", "HEAD", cwd=repo).stdout.strip(),
                        "latest": f"+{behind} коммит(ов)"})
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"at": datetime.now().isoformat(), "updates": ups}, ensure_ascii=False))
    return ups


def apply(target: str) -> tuple[bool, str]:
    """Обновить одно: plugin:<id> → claude plugin update; skill:<dir> → git pull --ff-only."""
    typ, _, name = target.partition(":")
    if not re.match(r"^[A-Za-z0-9_.@+-]{1,120}$", name or ""):
        return False, "непонятная цель"
    if typ == "plugin":
        installed = json.loads((PLUGINS / "installed_plugins.json").read_text()).get("plugins", {})
        if name not in installed:
            return False, "такого плагина нет"
        r = _run("claude", "plugin", "update", name, timeout=300)
        ok = r.returncode == 0
    elif typ == "skill":
        repo = HOME / ".claude" / "skills" / name
        if not (repo / ".git").exists() or repo.is_symlink():
            return False, "это не git-скилл"
        r = _run("git", "pull", "--ff-only", cwd=repo, timeout=120)
        ok = r.returncode == 0
    else:
        return False, "непонятная цель"
    if ok and CACHE.exists():  # убрать из кеша, чтобы не предлагать снова
        c = json.loads(CACHE.read_text())
        c["updates"] = [u for u in c["updates"] if u["id"] != target]
        CACHE.write_text(json.dumps(c, ensure_ascii=False))
    return ok, ((r.stdout or r.stderr).strip()[-300:] or "готово")


if __name__ == "__main__":
    print(json.dumps(check(force=True), ensure_ascii=False, indent=1))
