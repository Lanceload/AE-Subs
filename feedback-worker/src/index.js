// Посредник между AE Subs и Telegram-ботом обратной связи.
// Программа отправляет отзыв сюда (POST /feedback), а сервер пересылает его владельцу бота.
// Токен бота хранится только в секретах Cloudflare: TG_TOKEN, OWNER_CHAT_ID, WEBHOOK_SECRET.

import { DurableObject } from "cloudflare:workers";

const KINDS = {
  bug: { icon: "🐞", title: "Ошибка", tag: "#ошибка" },
  idea: { icon: "💡", title: "Предложение", tag: "#предложение" },
  review: { icon: "⭐", title: "Отзыв", tag: "#отзыв" },
};

const STRANGER_REPLY =
  "👋 Привет! Это бот обратной связи <b>Yaqen</b>.\n\n" +
  "Сам он на сообщения не отвечает: сюда приходят отзывы, сообщения об ошибках и предложения " +
  "из программы <b>AE Subs</b> — автоматических субтитров для After Effects.\n\n" +
  "✍️ Чтобы написать разработчику, откройте AE Subs → <b>Справка → Обратная связь</b>.\n\n" +
  "⬇️ Скачать программу: https://github.com/Lanceload/AE-Subs/releases";

const OWNER_REPLY =
  "✅ Бот работает. Отзывы из AE Subs приходят сюда.\n\n" +
  "Нажмите на хэштег (#ошибка, #предложение, #отзыв), чтобы увидеть все сообщения этого типа.";

const MAX_TEXT = 3000;

// Счётчик сообщений: один объект на адрес отправителя и один общий — Cloudflare гарантирует,
// что каждый объект существует в единственном экземпляре, поэтому счёт точный.
export class Limiter extends DurableObject {
  async hit(limit, periodMs) {
    const now = Date.now();
    const times = ((await this.ctx.storage.get("t")) || []).filter((t) => now - t < periodMs);
    const ok = times.length < limit;
    if (ok) times.push(now);
    await this.ctx.storage.put("t", times);
    return ok;
  }
}

async function allowed(env, ip) {
  const counter = (name) => env.LIMITER.get(env.LIMITER.idFromName(name));
  if (!(await counter("ip:" + ip).hit(3, 60_000))) return false;  // 3 в минуту с одного адреса
  return counter("global").hit(300, 86_400_000);  // и не больше 300 в сутки всего
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function clean(s, max) {
  return String(s ?? "").replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "").trim().slice(0, max);
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TG_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function formatFeedback(d) {
  const kind = KINDS[d.type] || KINDS.review;
  const version = clean(d.version, 20).replace(/[^0-9.]/g, "");
  const system = [d.system?.os, d.system?.gpu, d.system?.ae].map((s) => clean(s, 80)).filter(Boolean);
  const lines = [
    `${kind.icon} <b>${kind.title}</b>${version ? " · AE Subs " + esc(version) : ""}`,
    esc(clean(d.text, MAX_TEXT)),
    "─────────",
  ];
  if (system.length) lines.push("💻 " + esc(system.join(" · ")));
  lines.push(kind.tag + (version ? " #v" + version.replace(/\./g, "_") : ""));
  return lines.join("\n");
}

async function handleFeedback(request, env) {
  if (request.headers.get("X-AE-Subs") !== "1") return json({ ok: false, error: "forbidden" }, 403);
  // не больше нескольких сообщений в минуту с одного адреса
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (!(await allowed(env, ip))) return json({ ok: false, error: "Слишком часто. Попробуйте через минуту." }, 429);

  let d;
  try {
    d = await request.json();
  } catch {
    return json({ ok: false, error: "bad json" }, 400);
  }
  const text = clean(d.text, MAX_TEXT);
  if (text.length < 3) return json({ ok: false, error: "Сообщение слишком короткое" }, 400);

  const r = await tg(env, "sendMessage", {
    chat_id: env.OWNER_CHAT_ID,
    text: formatFeedback(d),
    parse_mode: "HTML",
    link_preview_options: { is_disabled: true },
  });
  if (!r.ok) return json({ ok: false, error: "Не удалось доставить сообщение" }, 502);
  return json({ ok: true });
}

async function handleTelegram(request, env) {
  if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET)
    return new Response("forbidden", { status: 403 });
  const update = await request.json().catch(() => ({}));
  const msg = update.message;
  if (msg && msg.chat && msg.chat.type === "private") {
    const owner = String(msg.chat.id) === String(env.OWNER_CHAT_ID);
    if (!owner || (msg.text || "").startsWith("/start")) {
      await tg(env, "sendMessage", {
        chat_id: msg.chat.id,
        text: owner ? OWNER_REPLY : STRANGER_REPLY,
        parse_mode: "HTML",
        link_preview_options: { is_disabled: true },
      });
    }
  }
  return new Response("ok");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/feedback") return handleFeedback(request, env);
    if (request.method === "POST" && url.pathname === "/telegram") return handleTelegram(request, env);
    if (request.method === "GET" && url.pathname === "/") return new Response("AE Subs feedback: ok");
    return new Response("not found", { status: 404 });
  },
};
