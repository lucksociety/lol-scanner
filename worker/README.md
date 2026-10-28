# Discord `/lol` interactions endpoint (Cloudflare Worker)

`https://lucksociety-lol-discord.dreiss324.workers.dev`

1. Verifies Discord's Ed25519 signature (`X-Signature-Ed25519` over `X-Signature-Timestamp + body`, 5-min replay window); anything else gets **401**.
2. Answers PING with PONG.
3. `/lol [date]` replies with a deferred response (type 5), dispatches `.github/workflows/discord-lol.yml`
   (inputs: date, interaction_token, application_id, channel_id, requested_at) and edits the reply to "🍀 Scanning…".
   The workflow freezes the forecast first, then fetches Jupiter odds (post-forecast), rebuilds the dashboard and
   PATCHes `webhooks/{app_id}/{token}/messages/@original`. Past Discord's 15-minute token window it posts to the channel with the bot token.

Server lock: `/lol` is a **guild command** registered only in the Luck Society server (`780172495268610059`); there is no
global command. The Worker also rejects any interaction whose `guild_id` is not `ALLOWED_GUILD_ID` (other servers and DMs
get a private "only available in the Luck Society server" reply; it fails closed if the var is unset), and the workflow
re-checks the `guild_id` input and that `channel_id` belongs to that server before it runs.

Secrets (never in this repo):
- Worker: `GITHUB_TOKEN`, set with `<token> | npx wrangler secret put GITHUB_TOKEN`. Recommended: a fine-grained PAT, repo `lucksociety/lol-scanner` only, permission **Actions: read & write**.
- Repo Actions: `DISCORD_BOT_TOKEN` (fallback channel posts), plus the scanner's `LOLESPORTS_API_KEY`/`JUP_API_KEY`.

Public values live in `wrangler.jsonc` (`DISCORD_APP_ID`, `DISCORD_PUBLIC_KEY`).

```
cd worker && npm i && npm test && npx wrangler deploy
```
