"""
Однократная настройка: создаёт бота через @BotFather (от аккаунта копировщика),
группу «на троих» (аккаунт копировщика + владелец + бот) и записывает всё в config.yaml.
Запуск: python setup_bot.py <username_владельца>
"""
import asyncio
import random
import re
import sys

import yaml
from telethon import TelegramClient, functions

OWNER = sys.argv[1] if len(sys.argv) > 1 else "notheine"
TITLE = "FxCopier — сделки"
CFG_PATH = "config.yaml"
cfg = yaml.safe_load(open(CFG_PATH, encoding="utf-8"))
tg = cfg["telegram"]
TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


async def create_bot(client):
    async with client.conversation("BotFather", timeout=40) as conv:
        await conv.send_message("/cancel")
        await conv.get_response()
        await conv.send_message("/newbot")
        r = await conv.get_response()
        print("BotFather:", r.raw_text[:80])
        await conv.send_message("FxCopier сделки")
        r = await conv.get_response()
        print("BotFather:", r.raw_text[:80])
        for _ in range(6):
            uname = f"fxcopier_{random.randint(1000, 99999)}_bot"
            await conv.send_message(uname)
            r = await conv.get_response()
            m = TOKEN_RE.search(r.raw_text)
            if m:
                return uname, m.group(0)
            print("BotFather:", r.raw_text[:100])
        raise SystemExit("BotFather не выдал токен")


async def main():
    client = TelegramClient("session", int(tg["api_id"]), tg["api_hash"])
    await client.connect()
    assert await client.is_user_authorized(), "аккаунт копировщика не авторизован"
    me = await client.get_me()
    owner = await client.get_entity(OWNER)

    if tg.get("bot_token") and tg.get("bot_username"):
        uname, token = tg["bot_username"], tg["bot_token"]
        print("Бот уже есть:", uname)
    else:
        uname, token = await create_bot(client)
        tg["bot_username"], tg["bot_token"] = uname, token
        yaml.safe_dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
        print("Создан бот:", uname)

    if not tg.get("report_chat"):
        res = await client(functions.messages.CreateChatRequest(users=[owner, uname], title=TITLE))
        upd = getattr(res, "updates", res)
        chat = upd.chats[0]
        tg["report_chat"] = -chat.id
        missing = [getattr(m, "user_id", None) for m in getattr(res, "missing_invitees", []) or []]
        yaml.safe_dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
        print("Создана группа:", TITLE, tg["report_chat"])
        if owner.id in missing:
            link = await client(functions.messages.ExportChatInviteRequest(peer=chat))
            await client.send_message(owner, f"Не получилось добавить вас в группу «{TITLE}» напрямую "
                                             f"(настройки приватности). Вступите по ссылке: {link.link}")
            print("Владелец не добавлен напрямую — отправлена ссылка-приглашение")

    tg["owner_ids"] = sorted({owner.id, me.id})
    tg["notify_chat"] = tg["report_chat"]
    yaml.safe_dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)

    bot = TelegramClient("bot_session", int(tg["api_id"]), tg["api_hash"])
    await bot.start(bot_token=tg["bot_token"])
    await bot.send_message(tg["report_chat"], "👋 Я бот копировщика. Сюда буду присылать сигналы из канала и "
                                              "каждое действие на счёте. Команды: /status /entry /pause /resume /closeall /help")
    await bot.disconnect()
    await client.disconnect()
    print("Готово: бот", uname, "пишет в группу", tg["report_chat"])


asyncio.run(main())
