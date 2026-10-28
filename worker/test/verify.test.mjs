// Local check of the Ed25519 verification logic with a throwaway keypair (no Discord secrets involved).
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
globalThis.crypto ??= webcrypto;
const { verifyDiscord } = await import("../src/index.js");
const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const pkHex = Buffer.from(await crypto.subtle.exportKey("raw", kp.publicKey)).toString("hex");
const ts = String(Math.floor(Date.now() / 1000)), body = '{"type":1}';
const sig = Buffer.from(await crypto.subtle.sign("Ed25519", kp.privateKey, new TextEncoder().encode(ts + body))).toString("hex");
assert.equal(await verifyDiscord(pkHex, sig, ts, body), true, "valid signature accepted");
assert.equal(await verifyDiscord(pkHex, sig, ts, body + " "), false, "tampered body rejected");
assert.equal(await verifyDiscord(pkHex, sig.replace(/^../, "00"), ts, body), false, "bad signature rejected");
assert.equal(await verifyDiscord(pkHex, sig, String(Number(ts) - 3600), body), false, "stale timestamp rejected");
assert.equal(await verifyDiscord(pkHex, "zz", ts, body), false, "malformed hex rejected");
assert.equal(await verifyDiscord(pkHex, null, ts, body), false, "missing header rejected");
console.log("worker signature tests: 6/6 passed");
