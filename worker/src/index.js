// 🐹 Stash — мгновенная прослойка между Telegram и GitHub Actions.
//
// Telegram шлёт сюда каждое сообщение (webhook). Worker:
//   1) проверяет секретный заголовок Telegram и что пишет владелец;
//   2) за ~1 сек отвечает «🐹 Хап!» (или снимает «часики» с кнопки);
//   3) кладёт апдейт в очередь (D1) и будит workflow в GitHub.
// Workflow забирает очередь через /drain и подтверждает обработку через /done.

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

function authorized(req, env) {
  const h = req.headers.get("authorization") || "";
  return safeEqual(h, `Bearer ${env.QUEUE_SECRET}`);
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
    return new Response("🐹", { status: 404 });
  },
};
