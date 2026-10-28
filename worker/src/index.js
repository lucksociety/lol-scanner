// Luck Society /lol Discord interactions endpoint (Cloudflare Worker).
// 1) verifies Discord's Ed25519 signature (401 on failure), 2) answers PING,
// 3) defers /lol (type 5) and dispatches the GitHub workflow that runs the scanner and edits the deferred reply.
// Secrets: env.GITHUB_TOKEN (wrangler secret). Nothing secret lives in this file.

const PING = 1, APPLICATION_COMMAND = 2;
const PONG = 1, DEFERRED_CHANNEL_MESSAGE = 5, CHANNEL_MESSAGE = 4;
const EPHEMERAL = 64;
const MAX_SKEW_S = 300;

function hexToBytes(hex) {
  if (!/^[0-9a-fA-F]*$/.test(hex) || hex.length % 2) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

export async function verifyDiscord(publicKeyHex, signatureHex, timestamp, body) {
  if (!signatureHex || !timestamp) return false;
  const sig = hexToBytes(signatureHex), pk = hexToBytes(publicKeyHex);
  if (!sig || sig.length !== 64 || !pk || pk.length !== 32) return false;
  const ts = Number(timestamp);
  if (!Number.isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > MAX_SKEW_S) return false;   // replay window
  const key = await crypto.subtle.importKey("raw", pk, { name: "Ed25519" }, false, ["verify"]);
  return crypto.subtle.verify("Ed25519", key, sig, new TextEncoder().encode(timestamp + body));
}

export function guildAllowed(env, it) {
  // Fail closed: no ALLOWED_GUILD_ID configured -> nothing is allowed. DMs have no guild_id -> rejected.
  return Boolean(env.ALLOWED_GUILD_ID) && typeof it.guild_id === "string" && it.guild_id === env.ALLOWED_GUILD_ID;
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
}

function todayET() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

async function dispatch(env, inputs) {
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.WORKFLOW_FILE}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "user-agent": "lucksociety-lol-discord-worker",
    },
    body: JSON.stringify({ ref: env.WORKFLOW_REF, inputs }),
  });
  if (r.status !== 204) throw new Error(`GitHub dispatch HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
}

async function editOriginal(env, token, content) {
  await fetch(`https://discord.com/api/v10/webhooks/${env.DISCORD_APP_ID}/${token}/messages/@original`, {
    method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ content }),
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") return new Response("Luck Society /lol interactions endpoint 🍀");
    if (request.method !== "POST") return new Response("method not allowed", { status: 405 });

    const body = await request.text();   // Discord interaction payloads are small (<= a few KB)
    const ok = await verifyDiscord(env.DISCORD_PUBLIC_KEY, request.headers.get("x-signature-ed25519"),
      request.headers.get("x-signature-timestamp"), body).catch(() => false);
    if (!ok) return new Response("invalid request signature", { status: 401 });

    const it = JSON.parse(body);
    if (it.type === PING) return json({ type: PONG });

    if (it.type === APPLICATION_COMMAND && !guildAllowed(env, it)) {
      console.log(JSON.stringify({ level: "warn", msg: "rejected guild", guild: it.guild_id || "DM" }));
      return json({ type: CHANNEL_MESSAGE, data: { content: "🍀 /lol is only available in the Luck Society server.", flags: EPHEMERAL } });
    }

    if (it.type === APPLICATION_COMMAND && it.data?.name === "lol") {
      const opt = (it.data.options || []).find((o) => o.name === "date");
      const date = (opt?.value || "").trim() || todayET();
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(date + "T00:00:00Z")))
        return json({ type: CHANNEL_MESSAGE, data: { content: "🍀 Date must be YYYY-MM-DD (America/New_York), e.g. 2026-10-28.", flags: EPHEMERAL } });
      const inputs = {
        date,
        interaction_token: it.token,
        application_id: it.application_id || env.DISCORD_APP_ID,
        guild_id: it.guild_id,
        channel_id: it.channel_id || it.channel?.id || "",
        requested_at: String(Math.floor(Date.now() / 1000)),
      };
      ctx.waitUntil(dispatch(env, inputs).then(() =>
        editOriginal(env, it.token, `🍀 Scanning ${date}… locking scores before we look at a single price 🔒`)
      ).catch(async (e) => {
        console.log(JSON.stringify({ level: "error", msg: "dispatch failed", error: String(e) }));
        await editOriginal(env, it.token, `🍀 Luck Society scanner could not start: ${String(e).slice(0, 180)}`);
      }));
      console.log(JSON.stringify({ level: "info", msg: "lol dispatched", date, guild: it.guild_id || null }));
      return json({ type: DEFERRED_CHANNEL_MESSAGE });
    }
    return json({ type: CHANNEL_MESSAGE, data: { content: "Unknown command.", flags: EPHEMERAL } });
  },
};
