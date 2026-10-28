# Discord `/lol` interactions endpoint (Cloudflare Worker)

`https://lucksociety-lol-discord.dreiss324.workers.dev`

1. Verifies Discord's Ed25519 signature (`X-Signature-Ed25519` over `X-Signature-Timestamp + body`, 5-min replay window); anything else gets **401**.
2. Answers PING with PONG.
3. `/lol [date]` replies with a deferred response (type 5), dispatches `.github/workflows/discord-lol.yml`
   (inputs: date, interaction_token, application_id, channel_id, requested_at) and edits the reply to "🍀 Scanning…".
   The workflow freezes the forecast first, then fetches Jupiter odds (post-forecast), rebuilds the dashboard and
   PATCHes `webhooks/{app_id}/{token}/messages/@original`. Past Discord's 15-minute token window it posts to the channel with the bot token.

Secrets (never in this repo):
- Worker: `GITHUB_TOKEN`, set with `<token> | npx wrangler secret put GITHUB_TOKEN`. Recommended: a fine-grained PAT, repo `lucksociety/lol-scanner` only, permission **Actions: read & write**.
- Repo Actions: `DISCORD_BOT_TOKEN` (fallback channel posts), plus the scanner's `LOLESPORTS_API_KEY`/`JUP_API_KEY`.

Public values live in `wrangler.jsonc` (`DISCORD_APP_ID`, `DISCORD_PUBLIC_KEY`).

```
cd worker && npm i && npm test && npx wrangler deploy
```
