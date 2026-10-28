// Date autocomplete + guild lock, exercised through the real fetch handler with a throwaway signing key.
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
globalThis.crypto ??= webcrypto;
const mod = await import("../src/index.js");
const { dateChoices, validYMD, todayET } = mod;

// ET date math
const late = new Date("2026-10-29T03:30:00Z");                 // 11:30 PM ET on Oct 28
assert.equal(todayET(late), "2026-10-28", "uses America/New_York, not UTC");
let c = dateChoices("", late);
assert.equal(c.length, 24); assert.ok(c.length <= 25);
assert.deepEqual(c.slice(0, 3).map((x) => x.name), ["Today · Wed Oct 28", "Tomorrow · Thu Oct 29", "Yesterday · Tue Oct 27"]);
assert.deepEqual(c.slice(0, 3).map((x) => x.value), ["2026-10-28", "2026-10-29", "2026-10-27"]);
assert.equal(c[3].value, "2026-10-30"); assert.equal(c[16].value, "2026-11-12"); assert.equal(c[17].value, "2026-10-26"); assert.equal(c[23].value, "2026-10-20");
assert.ok(c.every((x) => validYMD(x.value) && x.name.length <= 100), "values are YYYY-MM-DD, names within Discord limit");
assert.equal(new Set(c.map((x) => x.value)).size, c.length, "no duplicate dates");
const dst = dateChoices("", new Date("2026-11-01T12:00:00Z"));    // DST ends Nov 1
assert.deepEqual(dst.slice(0, 2).map((x) => x.value), ["2026-11-01", "2026-11-02"]);
// filtering
assert.deepEqual(dateChoices("tom", late).map((x) => x.value), ["2026-10-29"]);
assert.ok(dateChoices("nov", late).every((x) => x.name.includes("Nov")) && dateChoices("nov", late).length === 12);
assert.deepEqual(dateChoices("2026-11-05", late).map((x) => x.value), ["2026-11-05"]);
assert.deepEqual(dateChoices("11-05", late).map((x) => x.value), ["2026-11-05"]);
assert.deepEqual(dateChoices("2026-12-25", late), [{ name: "Fri Dec 25 2026", value: "2026-12-25" }], "typed date outside the window still offered");
assert.deepEqual(dateChoices("12/25", late).map((x) => x.value), ["2026-12-25"]);
assert.deepEqual(dateChoices("2026-02-30", late), [], "invalid typed date offers nothing");
assert.equal(validYMD("2026-02-30"), false); assert.equal(validYMD("2026-10-28"), true);

// through the handler
const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const pk = Buffer.from(await crypto.subtle.exportKey("raw", kp.publicKey)).toString("hex");
const env = { DISCORD_PUBLIC_KEY: pk, ALLOWED_GUILD_ID: "780172495268610059", DISCORD_APP_ID: "1" };
const ctx = { waitUntil() {} };
async function call(obj, sign = true) {
  const body = JSON.stringify(obj), ts = String(Math.floor(Date.now() / 1000));
  const sig = sign ? Buffer.from(await crypto.subtle.sign("Ed25519", kp.privateKey, new TextEncoder().encode(ts + body))).toString("hex") : "00".repeat(64);
  const r = await mod.default.fetch(new Request("https://w/", { method: "POST", body, headers: { "x-signature-ed25519": sig, "x-signature-timestamp": ts } }), env, ctx);
  return [r.status, r.status === 200 ? await r.json() : null];
}
const ac = (guild, value) => ({ type: 4, guild_id: guild, data: { name: "lol", options: [{ type: 3, name: "date", value, focused: true }] } });
let [st, j] = await call(ac("780172495268610059", ""));
assert.equal(st, 200); assert.equal(j.type, 8); assert.equal(j.data.choices.length, 24); assert.match(j.data.choices[0].name, /^Today · /);
[st, j] = await call(ac("780172495268610059", "yest")); assert.equal(j.data.choices.length, 1); assert.match(j.data.choices[0].name, /^Yesterday/);
[st, j] = await call(ac("999", "")); assert.deepEqual(j, { type: 8, data: { choices: [] } }, "other guild: empty choices");
[st, j] = await call({ type: 4, data: { name: "lol", options: [{ name: "date", value: "", focused: true }] } }); assert.deepEqual(j.data.choices, [], "DM: empty choices");
[st] = await call(ac("780172495268610059", ""), false); assert.equal(st, 401, "unsigned autocomplete rejected");
[st, j] = await call({ type: 2, guild_id: "780172495268610059", data: { name: "lol", options: [{ name: "date", value: "2026-13-01" }] } });
assert.equal(j.type, 4); assert.equal(j.data.flags, 64, "bad typed date -> private error");
[st, j] = await call({ type: 2, guild_id: "999", data: { name: "lol" } }); assert.equal(j.data.flags, 64, "other guild command -> private rejection");
console.log("autocomplete tests: all 33 assertions passed");
