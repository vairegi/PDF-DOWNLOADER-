"""Run this LOCALLY (on your own computer), once, to generate the
TELEGRAM_SESSION string the bot needs on Render.

Steps:
  1. pip install telethon
  2. python session_gen.py
  3. Enter your api_id and api_hash (from https://my.telegram.org)
  4. Enter your phone number (+countrycode...) and the login code Telegram sends you
     (and your 2FA password if you have one)
  5. Copy the printed string into Render env var TELEGRAM_SESSION.

IMPORTANT: this session string is a FULL LOGIN to your Telegram account.
Treat it like a password — never commit it to git, never share it.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("api_id: ").strip())
api_hash = input("api_hash: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    me = client.get_me()
    print(f"\nLogged in as: {me.first_name} (id={me.id})")
    print("\nCopy this entire string into Render env var TELEGRAM_SESSION:\n")
    print(client.session.save())
