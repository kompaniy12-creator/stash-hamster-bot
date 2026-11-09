// 🐹 Stash — мгновенная прослойка между Telegram и GitHub Actions.
//
// Telegram шлёт сюда каждое сообщение (webhook). Worker:
//   1) проверяет секретный заголовок Telegram и что пишет владелец;
//   2) за ~1 сек отвечает «🐹 Хап!» (или снимает «часики» с кнопки);
//   3) кладёт апдейт в очередь (D1) и будит workflow в GitHub.
// Workflow забирает очередь через /drain и подтверждает обработку через /done.
//
// Дашборд «Норка» (Telegram Mini App):
//   GET  /app                 — страница приложения
//   POST /api/data            — данные (только владельцу: проверка подписи initData от Telegram)
//   POST /api/action          — кнопки «Установить»/«Выплюнуть» → команда в очередь хомяку
//   POST /snapshot/catalog    — облачный хомяк кладёт каталог (ключ QUEUE_SECRET)
//   POST /snapshot/inventory  — грузчик на Mac кладёт опись установленного (ключ DASH_SECRET)

import APP_HTML from "./app.html";

const ACKS = [
  "🐹 Хап! Затолкал в щёку. Обнюхиваю…",
  "🌰 О, добыча! Уже тащу в норку, сейчас разберусь…",
  "🐹 Беру! Дай минутку покрутить в лапках…",
  "🐹 Цап! Несу в кладовку, через минутку скажу, что это…",
];
const BUTTON_ACKS = {
  install: "🐹 Принято! Передаю грузчику…",
  later: "🌰 Припрятал!",
  skip: "💨 Тьфу! Выплюнул.",
};

function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function tg(env, method, payload) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  return r.json().catch(() => ({}));
}

async function wakeGitHub(env) {
  const r = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/stash.yml/dispatches`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.GH_TOKEN}`,
        accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        "user-agent": "stash-hamster-worker",
        "content-type": "application/json",
      },
      body: JSON.stringify({ ref: env.GH_REF || "main" }),
    },
  );
  if (!r.ok) console.log("github dispatch failed", r.status);
}

async function handleTelegram(req, env, ctx) {
  if (!safeEqual(req.headers.get("x-telegram-bot-api-secret-token") || "", env.WEBHOOK_SECRET)) {
    return new Response("forbidden", { status: 403 });
  }
  const update = await req.json().catch(() => null);
  if (!update || typeof update.update_id !== "number") return new Response("ok");

  const msg = update.message;
  const cb = update.callback_query;
  const fromId = String((msg && msg.from && msg.from.id) || (cb && cb.from && cb.from.id) || "");
  if (fromId !== String(env.OWNER_ID)) return new Response("ok"); // чужим хомяк не отвечает

  let ackId = null;
  if (msg) {
    const text = msg.text || "";
    if (text.startsWith("/")) {
      await tg(env, "sendChatAction", { chat_id: msg.chat.id, action: "typing" });
    } else {
      const r = await tg(env, "sendMessage", {
        chat_id: msg.chat.id,
        text: ACKS[Math.floor(Math.random() * ACKS.length)],
        reply_parameters: { message_id: msg.message_id },
      });
      ackId = (r.result && r.result.message_id) || null;
    }
  } else if (cb) {
    const action = String(cb.data || "").split(":")[0];
    await tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: BUTTON_ACKS[action] || "🐹" });
  } else {
    return new Response("ok");
  }

  await env.DB.prepare("INSERT OR IGNORE INTO updates (id, body, ack_id, created) VALUES (?, ?, ?, ?)")
    .bind(update.update_id, JSON.stringify(update), ackId, Date.now())
    .run();
  ctx.waitUntil(wakeGitHub(env));
  return new Response("ok");
}

function authorized(req, env, secret = env.QUEUE_SECRET) {
  const h = req.headers.get("authorization") || "";
  return !!secret && safeEqual(h, `Bearer ${secret}`);
}

// --- Mini App ---
async function hmac(key, msg) {
  const k = await crypto.subtle.importKey("raw", typeof key === "string" ? new TextEncoder().encode(key) : key,
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg)));
}
const hex = (b) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");

// https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
async function ownerFromInitData(initData, env) {
  if (!initData || initData.length > 4096) return null;
  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  if (!hash) return null;
  params.delete("hash");
  const check = [...params.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join("\n");
  const secret = await hmac("WebAppData", env.TELEGRAM_BOT_TOKEN);
  if (!safeEqual(hex(await hmac(secret, check)), hash)) return null;
  const age = Date.now() / 1000 - Number(params.get("auth_date") || 0);
  if (!(age >= 0 && age < 86400)) return null;
  let user;
  try { user = JSON.parse(params.get("user") || "{}"); } catch { return null; }
  return String(user.id) === String(env.OWNER_ID) ? user : null;
}

async function putSnapshot(env, name, body) {
  await env.DB.prepare("INSERT INTO snapshots (name, body, updated) VALUES (?, ?, ?) " +
    "ON CONFLICT(name) DO UPDATE SET body = excluded.body, updated = excluded.updated")
    .bind(name, body, Date.now()).run();
}

async function snapshot(req, env, name, secret) {
  if (!authorized(req, env, secret)) return new Response("forbidden", { status: 403 });
  const body = await req.text();
  if (body.length > 1_500_000) return new Response("too big", { status: 413 });
  JSON.parse(body); // мусор не храним
  await putSnapshot(env, name, body);
  return Response.json({ ok: true });
}

async function apiData(req, env) {
  const { initData } = await req.json().catch(() => ({}));
  if (!(await ownerFromInitData(initData, env))) return new Response("forbidden", { status: 403 });
  const { results } = await env.DB.prepare("SELECT name, body, updated FROM snapshots").all();
  const out = {};
  for (const r of results || []) out[r.name] = { data: JSON.parse(r.body), updated: r.updated };
  return Response.json(out, { headers: { "cache-control": "no-store" } });
}

async function apiAction(req, env, ctx) {
  const { initData, id, action } = await req.json().catch(() => ({}));
  const user = await ownerFromInitData(initData, env);
  if (!user) return new Response("forbidden", { status: 403 });
  const cmd = { install: "install", skip: "skip" }[action];
  if (!cmd || !Number.isInteger(id) || id <= 0) return new Response("bad request", { status: 400 });
  // Команда для хомяка, как будто владелец написал её в чат. Отрицательный id — чтобы не путать с настоящими.
  const uid = -Date.now();
  const text = `/${cmd} ${id}`;
  const update = {
    update_id: uid,
    message: {
      message_id: 0, date: Math.floor(Date.now() / 1000), text,
      chat: { id: Number(env.OWNER_ID), type: "private" },
      from: { id: Number(env.OWNER_ID), is_bot: false, first_name: user.first_name || "owner" },
      entities: [{ type: "bot_command", offset: 0, length: cmd.length + 1 }],
    },
  };
  await env.DB.prepare("INSERT INTO updates (id, body, ack_id, created) VALUES (?, ?, NULL, ?)")
    .bind(uid, JSON.stringify(update), Date.now()).run();
  ctx.waitUntil(wakeGitHub(env));
  return Response.json({ ok: true });
}

async function drain(req, env) {
  if (!authorized(req, env)) return new Response("forbidden", { status: 403 });
  const { results } = await env.DB.prepare(
    "SELECT id, body, ack_id FROM updates ORDER BY id LIMIT 50",
  ).all();
  return Response.json(results || []);
}

async function done(req, env) {
  if (!authorized(req, env)) return new Response("forbidden", { status: 403 });
  const { ids } = await req.json().catch(() => ({ ids: [] }));
  const clean = (ids || []).filter((x) => Number.isInteger(x)).slice(0, 100);
  if (clean.length) {
    await env.DB.prepare(`DELETE FROM updates WHERE id IN (${clean.map(() => "?").join(",")})`)
      .bind(...clean)
      .run();
  }
  return Response.json({ deleted: clean.length });
}

export default {
  async fetch(req, env, ctx) {
    const { pathname } = new URL(req.url);
    if (req.method === "POST" && pathname === "/tg") return handleTelegram(req, env, ctx);
    if (req.method === "POST" && pathname === "/drain") return drain(req, env);
    if (req.method === "POST" && pathname === "/done") return done(req, env);
    if (req.method === "GET" && pathname === "/app") {
      return new Response(APP_HTML, { headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "content-security-policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://telegram.org; " +
          "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; " +
          "img-src 'self' data:; connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org",
      } });
    }
    if (req.method === "POST" && pathname === "/api/data") return apiData(req, env);
    if (req.method === "POST" && pathname === "/api/action") return apiAction(req, env, ctx);
    if (req.method === "POST" && pathname === "/snapshot/catalog") return snapshot(req, env, "catalog", env.QUEUE_SECRET);
    if (req.method === "POST" && pathname === "/snapshot/inventory") return snapshot(req, env, "inventory", env.DASH_SECRET);
    return new Response("🐹", { status: 404 });
  },
};
