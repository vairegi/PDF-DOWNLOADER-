# PDF Bot v4 — Telegra.ph middleman, OOM-safe compile

## What v4 fixes (from your Render log 2026-08-19)

1. **"Stuck at Compiling PDF" for 7+ minutes** — the log shows the whole
   service restarting at 11:32:27, right after the Telegra.ph reply at
   11:31:41. That is Render's OOM killer: the old compiler held all 48
   decoded hi-res pages in RAM at once (~330 MB) plus Pillow's slow PDF
   encoder, blowing the 512 MB free tier mid-compile. v4 streams: each page
   is decoded -> resized (max side 1600 px) -> JPEG-encoded -> closed, one
   at a time (~10 MB total), then img2pdf assembles the PDF with no
   re-encode. Compile now takes seconds, not minutes.
2. **First deploy crash** — `Added route will never be executed, method HEAD
   is already registered`: aiohttp's `add_get` already registers HEAD, so the
   extra `add_head` line crashed the app before it started. Removed (the
   UptimeRobot HEAD pings still get a 200).

## How it works

```
user ──nhentai link──> YOUR bot (BotFather/aiogram)
                          │
                          ▼
                 userbot (Telethon, your account)
                 sends link to @nHentaiBot
                 polls dialog <=30s for a telegra.ph reply
                          │
                          ▼
                 aiohttp downloads every image
                 from the telegra.ph article (no anti-bot)
                          │
                          ▼
                 img2pdf compiles PDF (<50 MB), streamed, low RAM
                          │
                          ▼
                 bot sends PDF to the user
```

## One-time setup (locally)

1. `pip install telethon`
2. `python session_gen.py` -> enter api_id / api_hash (https://my.telegram.org),
   phone number, login code, 2FA password if enabled.
3. Copy the printed session string. **It is a full login to your account —
   treat it like a password, never commit it.**

## Render env vars

| Var | Value |
|---|---|
| `BOT_TOKEN` | BotFather token (same as before) |
| `API_ID` | from my.telegram.org |
| `API_HASH` | from my.telegram.org |
| `TELEGRAM_SESSION` | string from session_gen.py |
| `NH_BOT_USERNAME` | optional, default `nHentaiBot` |
| `NH_TIMEOUT` | optional, default `30` (seconds to wait for reply) |
| `PAGE_CAP` | optional, default `120` |
| `CONCURRENCY` | optional, default `12` |

Build: `pip install -r requirements.txt` — Start: `python bot.py`
(`render.yaml` keeps working; `img2pdf` is the one new dependency).

## Behavior

- Accepts any `nhentai.net/g/<id>` link (bare or with a page number).
- Anything else -> "❌ Only nhentai.net gallery links are supported."
- @nHentaiBot silent for 30 s -> "❌ @nHentaiBot did not respond — try again later"
- Live progress: page counter + percent bar, throttled to Telegram edit rate.
- If a compile/upload still fails, the status message now shows the actual
  error instead of freezing.

## Notes

- The userbot runs as YOUR Telegram account (bots cannot message bots).
  Consider a secondary account.
- Simultaneous user requests are serialized through the one @nHentaiBot
  dialog so replies cannot cross-match.
- If @nHentaiBot ever changes its reply format, detection is in
  `scraper/userbot_bridge.py::_extract_telegraph_url`.
