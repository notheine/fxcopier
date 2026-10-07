"""Все сообщения канала с даты (только чтение). На сервере:
  cp /home/fx/copier/session.session /tmp/hist.session && chown fx /tmp/hist.session
  sudo -u fx /home/fx/copier/venv/bin/python fetch_channel.py 2026-05-01 /home/fx/data/channel.json
Копия сессии — чтобы не мешать работающему копировщику. Результат: [{id, ts (UTC-эпоха), text, reply}] по возрастанию."""
import asyncio, json, sys, datetime as dt
import yaml
from telethon import TelegramClient
t = yaml.safe_load(open("/home/fx/copier/config.yaml"))["telegram"]
since = dt.datetime.fromisoformat(sys.argv[1]).replace(tzinfo=dt.timezone.utc)


async def main():
    c = TelegramClient("/tmp/hist", t["api_id"], t["api_hash"])
    await c.connect()
    ch = None
    async for d in c.iter_dialogs():
        if t.get("channel", "Win Win") in d.name:
            ch = d.entity
            break
    out = []
    async for m in c.iter_messages(ch, limit=None):
        if m.date < since:
            break
        out.append({"id": m.id, "ts": int(m.date.timestamp()), "text": m.message or "",
                    "reply": m.reply_to.reply_to_msg_id if m.reply_to else None})
    json.dump(out[::-1], open(sys.argv[2], "w"), ensure_ascii=False)
    print(len(out))
    await c.disconnect()
asyncio.run(main())
