# Discord `/lol` interactions endpoint (Cloudflare Worker)

`https://lucksociety-lol-discord.dreiss324.workers.dev`

1. Verifies Discord's Ed25519 signature (`X-Signature-Ed25519` over `X-Signature-Timestamp + body`, 5-min replay window); anything else gets **401**.
2. Answers PING with PONG.
3. `/lol [date]` replies with a deferred response (type 5), dispatches `.github/workflows/discord-lol.yml`
   (inputs: date, interaction_token, application_id, channel_id, requested_at) and edits the reply to "🍀 Scanning…".
   The workflow freezes the forecast first, then fetches Jupiter odds (post-forecast), rebuilds the dashboard and
   PATCHes `webhooks/{app_id}/{token}/messages/@original`. Past Discord's 15-minute token window it posts to the channel with the bot token.
   If the dispatch returns anything but 204 (or the GITHUB_TOKEN secret is missing) the Worker edits the reply to
   "🍀 Luck Society scanner could not start (GitHub dispatch HTTP …)". The workflow always answers: when the date has no
   frozen forecast yet it first edits the reply to say it is building one from scratch (this is the slow path: gol.gg refresh,
   draft-model fit, 100k sims), and on any failure, step timeout (forecast 45 min) or cancellation it posts a red error embed.
   The gol.gg / Data Dragon cache is saved after every run so the next fresh build is incremental.

Date picker: the `date` option uses Discord **autocomplete** (Discord has no native date picker). The Worker answers
type-4 autocomplete requests with up to 25 type-8 choices computed in America/New_York: `Today · Wed Oct 28`,
`Tomorrow · Thu Oct 29`, `Yesterday · Tue Oct 27`, the next 14 days, then the past 7 (value = `YYYY-MM-DD`).
Typing filters by label (`tom`, `nov`, `fri`) or by date (`2026-11-05`, `11-05`, `12/25`; a valid date outside the window is
still offered). A manually typed `YYYY-MM-DD` is still accepted; blank = today ET. Autocomplete from other servers or DMs
gets an empty list. Choices are computed locally (no network), so they return well inside Discord's 3-second limit;
series counts are not shown because the Worker has no cached schedule to read cheaply.

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
