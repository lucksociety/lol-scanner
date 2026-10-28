// startScan: dispatch result -> what the user sees in the deferred reply. Uses a mocked fetch (no network).
import assert from "node:assert/strict";
import { startScan } from "../src/index.js";

const env = { GITHUB_TOKEN: "x", GITHUB_REPO: "o/r", WORKFLOW_FILE: "w.yml", WORKFLOW_REF: "main", DISCORD_APP_ID: "1" };
let n = 0;
async function scenario(name, ghStatus, envOver = {}, throwNet = false) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    if (String(url).includes("api.github.com")) {
      if (throwNet) throw new TypeError("network down");
      return new Response(ghStatus === 204 ? null : '{"message":"Bad credentials"}', { status: ghStatus });
    }
    return new Response("{}", { status: 200 });
  };
  const ok = await startScan({ ...env, ...envOver }, "tok", "2026-10-29", { date: "2026-10-29" });
  return { ok, calls, edits: calls.filter((c) => c.url.includes("discord.com")).map((c) => JSON.parse(c.init.body).content) };
}
const t = (c, m) => { assert.ok(c, m); n++; };

let r = await scenario("204", 204);
t(r.ok === true, "204 -> ok");
t(r.edits.length === 1 && r.edits[0].startsWith("🍀 Scanning 2026-10-29"), "204 -> Scanning edit");
const gh = r.calls.find((c) => c.url.includes("api.github.com"));
const b = JSON.parse(gh.init.body);
t(gh.url.endsWith("/repos/o/r/actions/workflows/w.yml/dispatches") && b.ref === "main" && b.inputs.date === "2026-10-29", "dispatch url/ref/inputs");

for (const st of [401, 404, 422, 500, 200]) {
  r = await scenario(String(st), st);
  t(r.ok === false && r.edits.length === 1 && r.edits[0].includes("could not start") && r.edits[0].includes(`HTTP ${st}`), `${st} -> error edit with status`);
}
r = await scenario("net", 204, {}, true);
t(r.ok === false && r.edits[0].includes("could not start"), "network error -> error edit");
r = await scenario("nosecret", 204, { GITHUB_TOKEN: "" });
t(r.ok === false && r.edits[0].includes("GITHUB_TOKEN") && !r.calls.some((c) => c.url.includes("github")), "missing secret -> error edit, no dispatch");
console.log(`dispatch: ${n} assertions passed`);
