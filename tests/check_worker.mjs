// Логика Worker без Cloudflare: подпись Mini App, секрет вебхука, чужие пользователи, будильник.
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHmac } from "node:crypto";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../worker/src/index.js", import.meta.url), "utf8")
  .replace('import APP_HTML from "./app.html";', 'const APP_HTML = "<html>app</html>";')
  + "\nexport { ownerFromInitData, safeEqual };\n";
const file = join(mkdtempSync(join(tmpdir(), "stash-")), "worker.mjs");
writeFileSync(file, src);
const w = await import(file);

const TOKEN = "123:TESTTOKEN", OWNER = "42";
function initData(userId, { token = TOKEN, ageSec = 10 } = {}) {
  const p = new URLSearchParams({ auth_date: String(Math.floor(Date.now() / 1000) - ageSec), query_id: "q", user: JSON.stringify({ id: userId, first_name: "S" }) });
  const check = [...p.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join("\n");
  const secret = createHmac("sha256", "WebAppData").update(token).digest();
  p.set("hash", createHmac("sha256", secret).update(check).digest("hex"));
  return p.toString();
}
function fakeEnv() {
  const rows = [], snaps = new Map();
  const stmt = (sql) => ({ sql, args: [], bind(...a) { this.args = a; return this; },
    async run() { if (/INSERT.*updates/.test(sql)) rows.push(this.args); if (/INSERT INTO snapshots/.test(sql)) snaps.set(this.args[0], { body: this.args[1], updated: this.args[2] }); return {}; },
    async all() { return { results: [] }; },
    async first() { return /alert_gh/.test(sql) ? snaps.get("alert_gh") || null : null; } });
  return { rows, snaps, TELEGRAM_BOT_TOKEN: TOKEN, OWNER_ID: OWNER, WEBHOOK_SECRET: "hook", QUEUE_SECRET: "queue", DASH_SECRET: "dash",
    GH_REPO: "o/r", GH_TOKEN: "t", DB: { prepare: stmt } };
}
const env = fakeEnv();
const ctx = { tasks: [], waitUntil(p) { this.tasks.push(p); } };
const calls = [];
let ghStatus = 204;
globalThis.fetch = async (url, opts) => {
  calls.push(String(url));
  if (String(url).includes("api.github.com")) return new Response(null, { status: ghStatus });
  return new Response(JSON.stringify({ ok: true, result: { message_id: 7 } }), { headers: { "content-type": "application/json" } });
};
const post = (path, body, headers = {}) => w.default.fetch(new Request("https://x" + path, { method: "POST", body: JSON.stringify(body), headers }), env, ctx);

// подпись Mini App
assert.ok(await w.ownerFromInitData(initData(42), env), "владелец с верной подписью проходит");
assert.equal(await w.ownerFromInitData(initData(7), env), null, "чужой пользователь не проходит");
assert.equal(await w.ownerFromInitData(initData(42, { token: "999:OTHER" }), env), null, "подпись чужим токеном не проходит");
assert.equal(await w.ownerFromInitData(initData(42, { ageSec: 90000 }), env), null, "протухшая подпись не проходит");
assert.equal(await w.ownerFromInitData(initData(42).replace("first_name", "first_nam3"), env), null, "подделанные данные не проходят");

// закрытые ручки
assert.equal((await post("/api/data", { initData: "" })).status, 403);
assert.equal((await post("/api/action", { initData: initData(7), id: 1, action: "install" })).status, 403);
assert.equal((await post("/drain", {})).status, 403);
assert.equal((await post("/snapshot/inventory", {}, { authorization: "Bearer queue" })).status, 403, "опись принимается только по своему ключу");
assert.equal((await post("/api/action", { initData: initData(42), action: "remove", target: ["skill:ok", "skill:../../x"] })).status, 400);
assert.equal((await post("/api/action", { initData: initData(42), action: "nope", id: 1 })).status, 400);

// действие владельца попадает в очередь как команда
env.rows.length = 0;
assert.equal((await post("/api/action", { initData: initData(42), action: "reanalyze", id: 5 })).status, 200);
assert.equal(JSON.parse(env.rows[0][1]).message.text, "/reanalyze 5");

// вебхук Telegram
const upd = (from) => ({ update_id: 100 + from, message: { message_id: 1, text: "https://x.y", chat: { id: from }, from: { id: from } } });
assert.equal((await post("/tg", upd(42), { "x-telegram-bot-api-secret-token": "wrong" })).status, 403);
env.rows.length = 0; calls.length = 0;
await post("/tg", upd(7), { "x-telegram-bot-api-secret-token": "hook" });
assert.equal(env.rows.length, 0, "сообщение чужого не попадает в очередь"); assert.equal(calls.length, 0, "чужому не отвечаем");
await post("/tg", upd(42), { "x-telegram-bot-api-secret-token": "hook" });
await Promise.all(ctx.tasks);
assert.equal(env.rows.length, 1); assert.ok(calls.some((u) => u.includes("api.github.com")), "будим GitHub");

// сломанный будильник: предупреждаем владельца, но не чаще раза в 6 часов
ghStatus = 401; calls.length = 0; ctx.tasks.length = 0;
await post("/tg", { ...upd(42), update_id: 201 }, { "x-telegram-bot-api-secret-token": "hook" });
await post("/tg", { ...upd(42), update_id: 202 }, { "x-telegram-bot-api-secret-token": "hook" });
await Promise.all(ctx.tasks);
assert.equal(calls.filter((u) => u.includes("/sendMessage")).length, 3, "2 ответа «перехвачен» + 1 предупреждение");

// страница приложения: ETag и 304
const r1 = await w.default.fetch(new Request("https://x/app"), env, ctx);
const r2 = await w.default.fetch(new Request("https://x/app", { headers: { "if-none-match": r1.headers.get("etag") } }), env, ctx);
assert.equal(r1.status, 200); assert.equal(r2.status, 304);
console.log("worker ok");
