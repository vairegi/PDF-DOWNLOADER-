# PDF Bot v3 — Telegra.ph middleman architecture

## How it works now

```
user ──nhentai link──> YOUR bot (BotFather/aiogram)
                          │
                          ▼
                 userbot (Telethon, your account)
                 sends link to @nHentaiBot
                          │
                          ▼
                 polls dialog ≤30s for reply
                 containing a telegra.ph URL
                          │
                          ▼
                 aiohttp downloads every image
                 from the telegra.ph article
                 (no Cloudflare, no IP blocks)
                          │
                          ▼
                 Pillow compiles PDF (<50 MB)
                          │
                          ▼
                 bot sends PDF to the user
```

## One-time setup (locally)

1. `pip install telethon`
2. `python session_gen.py` → enter your `api_id` / `api_hash` from
   https://my.telegram.org, your phone number, the login code, and your 2FA
   password if enabled.
3. It prints a long session string. **This is a full login to your account —
   treat it like a password.**

## Render env vars

| Var | Value |
|---|---|
| `BOT_TOKEN` | BotFather token (same as before) |
| `API_ID` | from my.telegram.org |
| `API_HASH` | from my.telegram.org |
| `TELEGRAM_SESSION` | the string from session_gen.py |
| `NH_BOT_USERNAME` | optional, default `nHentaiBot` |
| `NH_TIMEOUT` | optional, default `30` (seconds to wait for reply) |
| `PAGE_CAP` | optional, default `120` |
| `CONCURRENCY` | optional, default `12` |

Build: `pip install -r requirements.txt` — Start: `python bot.py`
(same as before; render.yaml keeps working).

## Behavior

- Accepts any `nhentai.net/g/<id>` link (with or without a page number).
- Anything else → "❌ Only nhentai.net gallery links are supported."
- @nHentaiBot silent for 30 s → "❌ @nHentaiBot did not respond — try again later"
  (no scraper fallback, per your choice).
- Live progress: page counter + percent bar, throttled to Telegram's edit rate.

## Important notes

- **The userbot runs as YOUR Telegram account.** Bots can't message bots, so
  this roundtrip is only possible from a user account. This is the same
  technique all "middleman" bots use, but know that Telegram ToS formally
  restricts account automation — use a secondary account if you prefer.
- Multiple simultaneous user requests are serialized through one dialog with
  @nHentaiBot (one at a time) — otherwise replies could be mismatched to the
  wrong request.
- If @nHentaiBot changes its reply format (e.g. stops using telegra.ph),
  detection is in `scraper/userbot_bridge.py::_extract_telegraph_url`.
