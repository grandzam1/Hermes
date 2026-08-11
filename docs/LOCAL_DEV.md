# Local Development (No Docker)

Hermes can run locally without Docker Compose. Use this when you want a lighter stack on WSL/Linux with Redis, the API, Celery, and the Vite frontend.

## Quick start

```bash
# From repo root — requires Redis, uv, pnpm, Node 22+
./scripts/local-dev.sh start
# or
pnpm local:start
```

| Command | Alias | Purpose |
|---------|-------|---------|
| `./scripts/local-dev.sh start` | `pnpm local:start` | Start Redis + API + Celery + frontend |
| `./scripts/local-dev.sh stop` | `pnpm local:stop` | Stop local services |
| `./scripts/local-dev.sh restart` | `pnpm local:restart` | Full restart (use after `.env` / cookie changes) |
| `./scripts/local-dev.sh status` | `pnpm local:status` | Health check |

> **Note:** Root `pnpm dev` still targets Docker Compose. Prefer `local:*` for the no-Docker path.

### URLs

- Frontend: http://127.0.0.1:5174
- API: http://127.0.0.1:8000
- API docs: http://127.0.0.1:8000/docs

### Logs

- API: `/tmp/hermes-api.log`
- Celery: `/tmp/hermes-celery.log`
- App: `/tmp/hermes-app.log`

### Local paths (API cwd: `packages/hermes-api/`)

| Path | Purpose |
|------|---------|
| `downloads/` | Completed files |
| `temp/` | In-progress downloads |
| `data/hermes.db` | SQLite database |
| `cookies/*.json` | Browser cookie exports (gitignored) |

Root `data/` is for Docker volumes and is unused in this mode.

## Cookies

Sites like YouTube and TikTok often require authenticated browser cookies.

1. Copy examples and fill with browser-exported JSON:

```bash
cp packages/hermes-api/cookies/youtube.json.example packages/hermes-api/cookies/youtube.json
cp packages/hermes-api/cookies/tiktok.json.example packages/hermes-api/cookies/tiktok.json
```

2. Set in `.env` / `packages/hermes-api/.env`:

```bash
HERMES_COOKIES_JSON=./cookies/youtube.json,./cookies/tiktok.json
```

3. Restart the full stack so **API and Celery** both reload settings:

```bash
./scripts/local-dev.sh restart
```

Hermes merges the JSON files into a Netscape cookie file (`cookies/combined.txt`) for yt-dlp. Do not commit real cookie files.

## TikTok / yt-dlp hardening

TikTok (especially from hosting/datacenter IPs) may need browser impersonation plus a short User-Agent. Defaults are **TikTok-only** so YouTube stays unchanged.

```bash
HERMES_YTDLP_IMPERSONATE=chrome          # or off
HERMES_YTDLP_IMPERSONATE_SITES=tiktok    # host fragments, or all
HERMES_YTDLP_USER_AGENT=Mozilla/5.0      # or off
```

Dependencies: yt-dlp nightly + `curl_cffi` (see `packages/hermes-api/pyproject.toml`).

### `/info` behavior

`GET /api/v1/info` normally probes with `extract_flat=True` to detect playlists. TikTok often fails in flat mode, so Hermes **skips the flat probe for TikTok hosts** and runs a full extract instead.

### Operational notes

- After changing `.env` or cookie JSON, always `restart` (Celery does not hot-reload these).
- TikTok can still intermittently challenge or block some IPs (“Unexpected response”, IP blocked). Retry or try another URL.
- YouTube “Sign in to confirm you’re not a bot” usually means expired/missing cookies — refresh `youtube.json` and restart.

## Related docs

- [Configuration Guide](CONFIGURATION.md) — all `HERMES_*` variables
- Cursor rule: `.cursor/rules/40-local-dev.mdc`
